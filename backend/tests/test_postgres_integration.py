"""Opt-in PostgreSQL integration coverage.

Run with TRIP_RUN_DB_INTEGRATION=true against the configured development DB.
The test removes only the records it created.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import settings
from app.graph.state import IntentConstraints
from app.main import app
from app.persistence.models import PlanningJob as PlanningJobRecord, Trip, TripExport, TripVersion
from app.services.export_service import cleanup_old_exports
from app.services.job_service import DatabaseJobStore
from app.services.trip_service import trip_service


pytestmark = pytest.mark.skipif(
    os.getenv("TRIP_RUN_DB_INTEGRATION", "").lower() not in {"1", "true", "yes"},
    reason="set TRIP_RUN_DB_INTEGRATION=true to run against PostgreSQL",
)


def test_database_job_persists_events_saved_versions_and_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "persistence_backend", "database")
    store = DatabaseJobStore()
    client = TestClient(app)
    job_id: str | None = None
    ad_hoc_trip_id: str | None = None
    api_export_file: Path | None = None
    try:
        submitted = store.submit(
            IntentConstraints(
                user_query="postgres integration smoke",
                destination="Shanghai",
                days=1,
                budget_limit=500,
                preferences=["museum"],
            )
        )
        job_id = submitted.id
        for _ in range(80):
            completed = store.get(job_id)
            if completed and completed.status in {"complete", "failed"}:
                break
            time.sleep(0.25)
        else:
            pytest.fail("database-backed planning job did not complete")

        assert completed is not None
        assert completed.status == "complete", completed.error
        event_window = store.events_since(job_id)
        assert event_window is not None
        assert event_window.next_offset == len(event_window.events)
        assert event_window.state is not None

        with store._session_factory() as session:
            record = session.get(PlanningJobRecord, UUID(job_id))
            assert record is not None
            assert record.trip_id is not None
            assert session.get(Trip, record.trip_id) is not None
            trip_id = record.trip_id.hex
        assert event_window.saved_trip_id == trip_id
        completed_job = store.get(job_id)
        assert completed_job is not None
        assert completed_job.saved_trip_id == trip_id

        assert any(item["id"] == trip_id for item in trip_service.list_saved_trips())
        saved_trip = trip_service.get_saved_trip(trip_id)
        assert saved_trip is not None
        assert saved_trip["state"]["intent_constraints"]["destination"] == "Shanghai"

        initial_version_id = saved_trip["current_version_id"]
        appended = trip_service.append_replanned_version(
            trip_id,
            event_window.state,
            initial_version_id,
        )
        assert appended["revision"] == 2
        assert appended["version_id"] != initial_version_id

        current = trip_service.get_saved_trip(trip_id)
        assert current is not None
        assert current["current_version_id"] == appended["version_id"]
        assert current["revision"] == 2
        with store._session_factory() as session:
            version = session.get(TripVersion, UUID(appended["version_id"]))
            assert version is not None
            assert version.parent_version_id == UUID(initial_version_id)
            assert version.change_reason == "manual_replan"

        with pytest.raises(RuntimeError, match="version changed"):
            trip_service.append_replanned_version(
                trip_id,
                event_window.state,
                initial_version_id,
            )

        save_response = client.post(
            "/api/trips/saved",
            json=event_window.state.model_dump(mode="json"),
        )
        assert save_response.status_code == 200, save_response.text
        ad_hoc_trip_id = save_response.json()["id"]
        export_response = client.post(
            f"/api/trips/saved/{ad_hoc_trip_id}/export/file",
            json={"export_format": "html"},
        )
        assert export_response.status_code == 200, export_response.text
        api_export = export_response.json()
        api_export_file = Path(api_export["file_path"])
        assert api_export_file.is_file()
        with store._session_factory() as session:
            api_record = session.get(TripExport, UUID(api_export["export_id"]))
            assert api_record is not None
            assert api_record.trip_version_id == UUID(api_export["trip_version_id"])
            assert api_record.status == "ready"

        export_file = tmp_path / "saved-trip.html"
        export_file.write_text("<html>saved trip</html>", encoding="utf-8")
        export = trip_service.register_export(
            trip_id,
            appended["version_id"],
            export_format="html",
            file_path=str(export_file),
            content_type="text/html",
        )
        exports = trip_service.list_exports(trip_id)
        assert exports is not None
        assert exports[0]["id"] == export["export_id"]
        assert exports[0]["trip_version_id"] == appended["version_id"]
        assert exports[0]["size_bytes"] == export_file.stat().st_size
        with store._session_factory() as session:
            assert session.get(TripExport, UUID(export["export_id"])) is not None

        stale_time = time.time() - (2 * 86400)
        os.utime(export_file, (stale_time, stale_time))
        assert cleanup_old_exports(tmp_path, max_age_days=1, max_files=100) == 1
        assert not export_file.exists()
        with store._session_factory() as session:
            expired = session.get(TripExport, UUID(export["export_id"]))
            assert expired is not None
            assert expired.status == "expired"
    finally:
        if api_export_file is not None:
            api_export_file.unlink(missing_ok=True)
        if job_id:
            with store._session_factory() as session:
                if ad_hoc_trip_id:
                    session.execute(delete(Trip).where(Trip.id == UUID(ad_hoc_trip_id)))
                record = session.get(PlanningJobRecord, UUID(job_id))
                if record and record.trip_id:
                    session.execute(delete(Trip).where(Trip.id == record.trip_id))
                session.execute(delete(PlanningJobRecord).where(PlanningJobRecord.id == UUID(job_id)))
                session.commit()
