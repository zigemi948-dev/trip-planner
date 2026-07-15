"""Fast database-schema checks that do not require a live PostgreSQL server."""

from app.core.database import Base
import app.persistence.models  # noqa: F401 - register mapped models


def test_persistence_metadata_contains_required_tables() -> None:
    expected = {
        "planning_jobs",
        "planning_job_events",
        "trips",
        "trip_versions",
        "trip_days",
        "trip_stops",
        "trip_exports",
    }
    assert expected.issubset(Base.metadata.tables)
