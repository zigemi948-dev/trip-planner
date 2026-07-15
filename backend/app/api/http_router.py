from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.agents.intent_agent import parse_trip_intent
from app.graph.state import (
    ExportRequest,
    IntentConstraints,
    IntentParseRequest,
    PlanningJob,
    PlanningJobEvents,
    PlanningJobSummary,
    ReplanRequest,
    SavedTripExportRequest,
    SavedTripReplanRequest,
    TripState,
    WorkflowTopology,
)
from app.graph.edges import workflow_topology
from app.graph.workflow import run_replan_workflow, run_trip_workflow
from app.services.export_service import persist_export_payload, render_export_payload
from app.services.job_service import job_store
from app.services.trip_service import trip_service

router = APIRouter(tags=["trips"])


@router.post("/trips/plan", response_model=TripState)
def plan_trip(intent: IntentConstraints) -> TripState:
    """Plan a trip from a normalized request payload."""
    return run_trip_workflow(intent)


@router.get("/trips/workflow/topology", response_model=WorkflowTopology)
def get_workflow_topology() -> WorkflowTopology:
    """Return the deterministic Map-Compute-Reduce workflow topology."""
    return workflow_topology()


@router.post("/trips/intent/parse", response_model=IntentConstraints)
def parse_intent(request: IntentParseRequest) -> IntentConstraints:
    """Parse a raw natural-language request into solver-safe constraints."""
    return parse_trip_intent(request.user_query)


@router.post("/trips/replan", response_model=TripState)
def replan_trip(request: ReplanRequest) -> TripState:
    """Repair one day of an existing trip after an itinerary edit."""
    return run_replan_workflow(request)


@router.post("/trips/export")
def export_trip(request: ExportRequest) -> dict[str, str]:
    """Return an export payload for the solved trip."""
    valid_formats = {"html", "pdf", "png"}
    if request.export_format not in valid_formats:
        raise HTTPException(
            status_code=422, 
            detail=f"Unsupported export format '{request.export_format}'. Permitted values: {', '.join(valid_formats)}"
        )
    return render_export_payload(request.solution, request.export_format, request.map_snapshot_base64)


@router.post("/trips/export/file")
def export_trip_file(request: ExportRequest) -> dict[str, str]:
    """Persist an export artifact and return its absolute file path."""
    valid_formats = {"html", "pdf", "png"}
    if request.export_format not in valid_formats:
        raise HTTPException(
            status_code=422, 
            detail=f"Unsupported export format '{request.export_format}'. Permitted values: {', '.join(valid_formats)}"
        )
    return persist_export_payload(request.solution, request.export_format, map_snapshot_base64=request.map_snapshot_base64)


@router.post("/trips/jobs", response_model=PlanningJob)
def submit_job(intent: IntentConstraints) -> PlanningJob:
    """Create a stored planning job for polling and event replay."""
    return job_store.submit(intent)


@router.get("/trips/jobs", response_model=list[PlanningJobSummary])
def list_jobs() -> list[PlanningJobSummary]:
    """List stored planning jobs."""
    return job_store.list()


@router.get("/trips/jobs/{job_id}", response_model=PlanningJob)
def get_job(job_id: str) -> PlanningJob:
    """Fetch one stored planning job."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/trips/jobs/{job_id}/events", response_model=PlanningJobEvents)
def get_job_events(job_id: str, after: int = Query(default=0, ge=0)) -> PlanningJobEvents:
    """Fetch workflow events after a zero-based event offset."""
    events = job_store.events_since(job_id, after)
    if events is None:
        raise HTTPException(status_code=404, detail="job not found")
    return events


@router.get("/trips/demo", response_model=TripState)
def demo_trip() -> TripState:
    """Return a deterministic demo plan for frontend development."""
    intent = IntentConstraints(
        user_query="Plan a balanced two-day city trip under budget.",
        destination="Shanghai",
        days=2,
        budget_limit=600,
        preferences=["museum", "food", "landmark"],
    )
    return run_trip_workflow(intent)


@router.get("/trips/saved")
def list_saved_trips() -> list[dict]:
    """List database-backed itineraries without loading each full solution."""
    return trip_service.list_saved_trips()


@router.post("/trips/saved")
def save_trip(state: TripState) -> dict:
    """Persist a solved UI state before version-aware edits or exports."""
    return trip_service.create_saved_trip(state)


@router.get("/trips/saved/{trip_id}")
def get_saved_trip(trip_id: str) -> dict:
    """Return the current immutable version of one saved itinerary."""
    trip = trip_service.get_saved_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="saved trip not found")
    return trip


@router.post("/trips/saved/{trip_id}/replan")
def replan_saved_trip(trip_id: str, request: SavedTripReplanRequest) -> dict:
    """Replan the current saved version and append an immutable successor."""
    saved_trip = trip_service.get_saved_trip(trip_id)
    if saved_trip is None:
        raise HTTPException(status_code=404, detail="saved trip not found")

    current_version_id = saved_trip["current_version_id"]
    expected_version_id = request.expected_version_id.hex if request.expected_version_id else current_version_id
    if expected_version_id != current_version_id:
        raise HTTPException(status_code=409, detail="saved trip version changed")

    replanned_state = run_replan_workflow(
        ReplanRequest(
            state=TripState.model_validate(saved_trip["state"]),
            day=request.day,
            new_poi=request.new_poi,
        )
    )
    try:
        version = trip_service.append_replanned_version(
            trip_id,
            replanned_state,
            expected_version_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="saved trip not found") from exc
    return {**version, "state": replanned_state.model_dump(mode="json")}


@router.post("/trips/saved/{trip_id}/export/file")
def export_saved_trip_file(trip_id: str, request: SavedTripExportRequest) -> dict:
    """Export the current saved version and register the artifact metadata."""
    saved_trip = trip_service.get_saved_trip(trip_id)
    if saved_trip is None:
        raise HTTPException(status_code=404, detail="saved trip not found")

    state = TripState.model_validate(saved_trip["state"])
    payload = persist_export_payload(
        state.routing_solution,
        request.export_format,
        map_snapshot_base64=request.map_snapshot_base64,
    )
    try:
        metadata = trip_service.register_export(
            trip_id,
            saved_trip["current_version_id"],
            export_format=request.export_format,
            file_path=payload["file_path"],
            content_type=payload["content_type"],
        )
    except (LookupError, ValueError) as exc:
        Path(payload["file_path"]).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="saved trip version not found") from exc
    except Exception:
        Path(payload["file_path"]).unlink(missing_ok=True)
        raise
    return {**payload, **metadata}


@router.get("/trips/saved/{trip_id}/exports")
def list_saved_trip_exports(trip_id: str) -> list[dict]:
    """List registered artifacts for every version of a saved trip."""
    exports = trip_service.list_exports(trip_id)
    if exports is None:
        raise HTTPException(status_code=404, detail="saved trip not found")
    return exports
