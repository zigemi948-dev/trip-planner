"""Persistence operations for saved itinerary aggregates."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.graph.state import TripState
from app.persistence.models import Trip, TripDay, TripExport, TripStop, TripVersion


class TripRepository:
    """Writes the normalized trip projection and immutable version snapshot."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_from_state(self, state: TripState, source_job_id: UUID | None = None) -> UUID:
        intent = state.intent_constraints
        trip = Trip(
            title=f"{intent.destination} {intent.days}-day itinerary",
            destination=intent.destination,
            days=intent.days,
            budget_limit=Decimal(str(intent.budget_limit)),
            status="active",
        )
        self.session.add(trip)
        self.session.flush()

        version = TripVersion(
            trip_id=trip.id,
            revision=1,
            source_job_id=source_job_id,
            intent_json=intent.model_dump(mode="json"),
            solution_json=state.routing_solution.model_dump(mode="json"),
            state_json=state.model_dump(mode="json"),
            change_reason="initial_plan",
        )
        self.session.add(version)
        self.session.flush()
        trip.current_version_id = version.id

        self._add_route_projection(version.id, state)
        return trip.id

    def append_version(
        self,
        trip_id: UUID,
        state: TripState,
        *,
        expected_version_id: UUID,
        change_reason: str = "manual_replan",
    ) -> tuple[UUID, int]:
        trip = self.session.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
        if trip is None or trip.status == "archived":
            raise LookupError("saved trip not found")
        if trip.current_version_id != expected_version_id:
            raise RuntimeError("saved trip version changed")

        revision = self.session.scalar(
            select(func.max(TripVersion.revision)).where(TripVersion.trip_id == trip_id)
        ) or 0
        version = TripVersion(
            trip_id=trip.id,
            revision=revision + 1,
            parent_version_id=trip.current_version_id,
            intent_json=state.intent_constraints.model_dump(mode="json"),
            solution_json=state.routing_solution.model_dump(mode="json"),
            state_json=state.model_dump(mode="json"),
            change_reason=change_reason,
        )
        self.session.add(version)
        self.session.flush()
        self._add_route_projection(version.id, state)
        trip.current_version_id = version.id
        trip.updated_at = datetime.now(timezone.utc)
        return version.id, version.revision

    def _add_route_projection(self, version_id: UUID, state: TripState) -> None:
        costs_by_day = {item.day: item.model_dump(mode="json") for item in state.routing_solution.daily_costs}
        weather_by_day = {item.day: item.model_dump(mode="json") for item in state.routing_solution.daily_weather}
        for route in state.routing_solution.optimized_route:
            day = TripDay(
                trip_version_id=version_id,
                day_number=route.day,
                daily_cost_json=costs_by_day.get(route.day, {}),
                weather_json=weather_by_day.get(route.day, {}),
            )
            self.session.add(day)
            self.session.flush()
            for position, stop in enumerate(route.stops):
                poi = stop.poi
                self.session.add(
                    TripStop(
                        trip_day_id=day.id,
                        position=position,
                        poi_external_id=poi.id,
                        name=poi.name,
                        category=poi.category,
                        latitude=Decimal(str(poi.coordinates.lat)),
                        longitude=Decimal(str(poi.coordinates.lng)),
                        arrival_time=stop.arrival_time,
                        departure_time=stop.departure_time,
                        stay_minutes=poi.visit_duration_minutes,
                        inbound_mode=stop.inbound_mode.value if stop.inbound_mode else None,
                        inbound_distance_km=Decimal(str(stop.inbound_distance_km)),
                        inbound_cost=Decimal(str(stop.inbound_cost)),
                        raw_json=stop.model_dump(mode="json"),
                    )
                )

    def register_export(
        self,
        trip_id: UUID,
        version_id: UUID,
        *,
        export_format: str,
        storage_path: Path,
        content_type: str,
        expires_at: datetime | None,
    ) -> TripExport:
        version = self.session.scalar(
            select(TripVersion).where(TripVersion.id == version_id, TripVersion.trip_id == trip_id)
        )
        if version is None:
            raise LookupError("saved trip version not found")
        record = TripExport(
            trip_version_id=version.id,
            export_format=export_format,
            storage_path=str(storage_path.resolve()),
            content_type=content_type,
            size_bytes=storage_path.stat().st_size,
            status="ready",
            expires_at=expires_at,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_exports(self, trip_id: UUID) -> list[dict]:
        rows = self.session.scalars(
            select(TripExport)
            .join(TripVersion, TripExport.trip_version_id == TripVersion.id)
            .where(TripVersion.trip_id == trip_id)
            .order_by(TripExport.created_at.desc())
        ).all()
        return [
            {
                "id": item.id.hex,
                "trip_version_id": item.trip_version_id.hex,
                "format": item.export_format,
                "file_path": item.storage_path,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "status": item.status,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "created_at": item.created_at.isoformat(),
            }
            for item in rows
        ]

    def reconcile_export_statuses(
        self,
        managed_root: Path,
        removed_paths: set[Path],
        *,
        now: datetime,
    ) -> dict[str, int]:
        """Reconcile ready export rows with files managed by one cleanup root."""
        root = managed_root.resolve()
        removed = {path.resolve() for path in removed_paths}
        counts = {"expired": 0, "missing": 0}
        rows = self.session.scalars(
            select(TripExport).where(TripExport.status == "ready")
        ).all()
        for record in rows:
            path = Path(record.storage_path).resolve()
            if path != root and root not in path.parents:
                continue

            expires_at = record.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if path in removed or (expires_at is not None and expires_at <= now):
                record.status = "expired"
                counts["expired"] += 1
            elif not path.is_file():
                record.status = "missing"
                counts["missing"] += 1
        return counts

    def list_summaries(self) -> list[dict]:
        trips = self.session.scalars(
            select(Trip).where(Trip.status != "archived").order_by(Trip.updated_at.desc())
        ).all()
        return [
            {
                "id": trip.id.hex,
                "title": trip.title,
                "destination": trip.destination,
                "days": trip.days,
                "budget_limit": float(trip.budget_limit),
                "status": trip.status,
                "current_version_id": trip.current_version_id.hex if trip.current_version_id else None,
                "updated_at": trip.updated_at.isoformat(),
            }
            for trip in trips
        ]

    def get_current_state(self, trip_id: UUID) -> dict | None:
        trip = self.session.get(Trip, trip_id)
        if trip is None or trip.status == "archived" or trip.current_version_id is None:
            return None
        version = self.session.get(TripVersion, trip.current_version_id)
        if version is None:
            return None
        return {
            "id": trip.id.hex,
            "title": trip.title,
            "destination": trip.destination,
            "days": trip.days,
            "budget_limit": float(trip.budget_limit),
            "status": trip.status,
            "current_version_id": version.id.hex,
            "revision": version.revision,
            "created_at": trip.created_at.isoformat(),
            "updated_at": trip.updated_at.isoformat(),
            "state": version.state_json,
        }
