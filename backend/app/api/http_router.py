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
    TripState,
    WorkflowTopology,
)
from app.graph.edges import workflow_topology
from app.graph.workflow import run_replan_workflow, run_trip_workflow
from app.services.export_service import persist_export_payload, render_export_payload
from app.services.job_service import job_store

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
    return render_export_payload(request.solution, request.export_format)


@router.post("/trips/export/file")
def export_trip_file(request: ExportRequest) -> dict[str, str]:
    """Persist an export artifact and return its absolute file path."""
    valid_formats = {"html", "pdf", "png"}
    if request.export_format not in valid_formats:
        raise HTTPException(
            status_code=422, 
            detail=f"Unsupported export format '{request.export_format}'. Permitted values: {', '.join(valid_formats)}"
        )
    return persist_export_payload(request.solution, request.export_format)


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