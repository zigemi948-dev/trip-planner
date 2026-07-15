"""Persist solver output as immutable, queryable trip versions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session_factory
from app.graph.state import TripState
from app.persistence.repositories.trip_repository import TripRepository


class TripService:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory

    def _sessions(self) -> Callable[[], Session]:
        return self._session_factory or get_session_factory()

    def create_from_completed_job(self, state: TripState, source_job_id: UUID) -> UUID:
        """Create an initial saved itinerary and a complete immutable snapshot."""
        with self._sessions()() as session:
            trip_id = TripRepository(session).create_from_state(state, source_job_id)
            session.commit()
            return trip_id

    def create_saved_trip(self, state: TripState) -> dict:
        """Persist an ad-hoc UI result so later edits and exports have an identity."""
        with self._sessions()() as session:
            repository = TripRepository(session)
            trip_id = repository.create_from_state(state)
            session.commit()
        saved = self.get_saved_trip(trip_id.hex)
        if saved is None:
            raise RuntimeError("saved trip could not be reloaded")
        return saved

    def list_saved_trips(self) -> list[dict]:
        with self._sessions()() as session:
            return TripRepository(session).list_summaries()

    def get_saved_trip(self, trip_id: str) -> dict | None:
        try:
            parsed_id = UUID(trip_id)
        except ValueError:
            return None
        with self._sessions()() as session:
            return TripRepository(session).get_current_state(parsed_id)

    def append_replanned_version(
        self,
        trip_id: str,
        state: TripState,
        expected_version_id: str,
    ) -> dict:
        """Append a replan snapshot only when the caller edited the current version."""
        parsed_trip_id = UUID(trip_id)
        parsed_version_id = UUID(expected_version_id)
        with self._sessions()() as session:
            version_id, revision = TripRepository(session).append_version(
                parsed_trip_id,
                state,
                expected_version_id=parsed_version_id,
            )
            session.commit()
        return {
            "trip_id": parsed_trip_id.hex,
            "version_id": version_id.hex,
            "revision": revision,
        }

    def register_export(
        self,
        trip_id: str,
        version_id: str,
        *,
        export_format: str,
        file_path: str,
        content_type: str,
    ) -> dict:
        """Register one generated artifact against the exact version it renders."""
        parsed_trip_id = UUID(trip_id)
        parsed_version_id = UUID(version_id)
        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.export_cleanup_max_age_days)
        with self._sessions()() as session:
            record = TripRepository(session).register_export(
                parsed_trip_id,
                parsed_version_id,
                export_format=export_format,
                storage_path=Path(file_path),
                content_type=content_type,
                expires_at=expires_at,
            )
            payload = {
                "export_id": record.id.hex,
                "trip_version_id": record.trip_version_id.hex,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            }
            session.commit()
            return payload

    def list_exports(self, trip_id: str) -> list[dict] | None:
        """Return export metadata for a live saved trip, newest first."""
        try:
            parsed_id = UUID(trip_id)
        except ValueError:
            return None
        with self._sessions()() as session:
            repository = TripRepository(session)
            if repository.get_current_state(parsed_id) is None:
                return None
            return repository.list_exports(parsed_id)

    def reconcile_export_statuses(
        self,
        managed_root: Path,
        removed_paths: set[Path],
    ) -> dict[str, int]:
        """Synchronize database export status after filesystem cleanup."""
        with self._sessions()() as session:
            counts = TripRepository(session).reconcile_export_statuses(
                managed_root,
                removed_paths,
                now=datetime.now(timezone.utc),
            )
            session.commit()
            return counts


trip_service = TripService()
