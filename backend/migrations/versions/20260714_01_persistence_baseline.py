"""Create PostgreSQL persistence tables for jobs and saved itineraries.

Revision ID: 20260714_01
Revises:
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260714_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The previous prototype created integer-keyed `trips` and `routes` tables
    # directly from application startup.  Preserve that data untouched so an
    # explicit import can be audited later, then create the versioned schema.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "trips" in existing_tables:
        legacy_columns = {column["name"] for column in inspector.get_columns("trips")}
        if {"id", "title", "destination", "budget"}.issubset(legacy_columns) and "days" not in legacy_columns:
            op.rename_table("trips", "legacy_trips")
        else:
            raise RuntimeError("Existing trips table is not the supported legacy prototype schema")
    if "routes" in existing_tables:
        legacy_columns = {column["name"] for column in inspector.get_columns("routes")}
        if {"id", "trip_id", "day_number", "location_name", "order_index"}.issubset(legacy_columns):
            op.rename_table("routes", "legacy_routes")
        else:
            raise RuntimeError("Existing routes table is not the supported legacy prototype schema")

    op.create_table(
        "trips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("destination", sa.String(length=128), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("budget_limit", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_trips_destination", "trips", ["destination"])
    op.create_index("ix_trips_owner_id", "trips", ["owner_id"])
    op.create_index("ix_trips_status", "trips", ["status"])
    op.create_index("ix_trips_current_version_id", "trips", ["current_version_id"])

    op.create_table(
        "planning_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("intent_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_planning_jobs_status", "planning_jobs", ["status"])
    op.create_index("ix_planning_jobs_created_status", "planning_jobs", ["created_at", "status"])

    op.create_table(
        "planning_job_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["job_id"], ["planning_jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "sequence", name="uq_planning_job_event_sequence"),
    )

    op.create_table(
        "trip_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("solution_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_reason", sa.String(length=64), nullable=False, server_default="initial_plan"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_job_id"], ["planning_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["trip_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("trip_id", "revision", name="uq_trip_version_revision"),
    )
    op.create_index("ix_trip_versions_trip_created", "trip_versions", ["trip_id", "created_at"])

    op.create_table(
        "trip_days",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("trip_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=True),
        sa.Column("daily_cost_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("weather_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["trip_version_id"], ["trip_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("trip_version_id", "day_number", name="uq_trip_day_number"),
    )

    op.create_table(
        "trip_stops",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("trip_day_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("poi_external_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("arrival_time", sa.String(length=8), nullable=True),
        sa.Column("departure_time", sa.String(length=8), nullable=True),
        sa.Column("stay_minutes", sa.Integer(), nullable=True),
        sa.Column("inbound_mode", sa.String(length=32), nullable=True),
        sa.Column("inbound_distance_km", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("inbound_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["trip_day_id"], ["trip_days.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("trip_day_id", "position", name="uq_trip_stop_position"),
    )

    op.create_table(
        "trip_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("trip_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("export_format", sa.String(length=8), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["trip_version_id"], ["trip_versions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_trip_exports_expires_at", "trip_exports", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_trip_exports_expires_at", table_name="trip_exports")
    op.drop_table("trip_exports")
    op.drop_table("trip_stops")
    op.drop_table("trip_days")
    op.drop_index("ix_trip_versions_trip_created", table_name="trip_versions")
    op.drop_table("trip_versions")
    op.drop_table("planning_job_events")
    op.drop_index("ix_planning_jobs_created_status", table_name="planning_jobs")
    op.drop_index("ix_planning_jobs_status", table_name="planning_jobs")
    op.drop_table("planning_jobs")
    op.drop_index("ix_trips_current_version_id", table_name="trips")
    op.drop_index("ix_trips_status", table_name="trips")
    op.drop_index("ix_trips_owner_id", table_name="trips")
    op.drop_index("ix_trips_destination", table_name="trips")
    op.drop_table("trips")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "legacy_routes" in inspector.get_table_names():
        op.rename_table("legacy_routes", "routes")
    if "legacy_trips" in inspector.get_table_names():
        op.rename_table("legacy_trips", "trips")
