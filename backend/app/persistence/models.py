"""PostgreSQL persistence schema for planning jobs and saved itineraries."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PlanningJob(Base):
    __tablename__ = "planning_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    intent_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trip_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[PlanningJobEvent]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="PlanningJobEvent.sequence")
    trip: Mapped[Trip | None] = relationship(back_populates="source_jobs")


class PlanningJobEvent(Base):
    __tablename__ = "planning_job_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_planning_job_event_sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("planning_jobs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job: Mapped[PlanningJob] = relationship(back_populates="events")


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    destination: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    current_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[TripVersion]] = relationship(back_populates="trip", cascade="all, delete-orphan", order_by="TripVersion.revision")
    source_jobs: Mapped[list[PlanningJob]] = relationship(back_populates="trip")


class TripVersion(Base):
    __tablename__ = "trip_versions"
    __table_args__ = (UniqueConstraint("trip_id", "revision", name="uq_trip_version_revision"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    trip_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("planning_jobs.id", ondelete="SET NULL"))
    parent_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_versions.id", ondelete="SET NULL"))
    intent_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    solution_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="initial_plan")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    trip: Mapped[Trip] = relationship(back_populates="versions")
    days: Mapped[list[TripDay]] = relationship(back_populates="trip_version", cascade="all, delete-orphan", order_by="TripDay.day_number")
    exports: Mapped[list[TripExport]] = relationship(back_populates="trip_version", cascade="all, delete-orphan")


class TripDay(Base):
    __tablename__ = "trip_days"
    __table_args__ = (UniqueConstraint("trip_version_id", "day_number", name="uq_trip_day_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trip_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_versions.id", ondelete="CASCADE"), nullable=False)
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[str | None] = mapped_column(String(10))
    daily_cost_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    weather_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    trip_version: Mapped[TripVersion] = relationship(back_populates="days")
    stops: Mapped[list[TripStop]] = relationship(back_populates="trip_day", cascade="all, delete-orphan", order_by="TripStop.position")


class TripStop(Base):
    __tablename__ = "trip_stops"
    __table_args__ = (UniqueConstraint("trip_day_id", "position", name="uq_trip_stop_position"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trip_day_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("trip_days.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    poi_external_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    arrival_time: Mapped[str | None] = mapped_column(String(8))
    departure_time: Mapped[str | None] = mapped_column(String(8))
    stay_minutes: Mapped[int | None] = mapped_column(Integer)
    inbound_mode: Mapped[str | None] = mapped_column(String(32))
    inbound_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    inbound_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    trip_day: Mapped[TripDay] = relationship(back_populates="stops")


class TripExport(Base):
    __tablename__ = "trip_exports"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    trip_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trip_versions.id", ondelete="CASCADE"), nullable=False)
    export_format: Mapped[str] = mapped_column(String(8), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    trip_version: Mapped[TripVersion] = relationship(back_populates="exports")


Index("ix_planning_jobs_created_status", PlanningJob.created_at, PlanningJob.status)
Index("ix_trip_versions_trip_created", TripVersion.trip_id, TripVersion.created_at)
