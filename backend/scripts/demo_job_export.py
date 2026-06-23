from pathlib import Path
import sys
from tempfile import mkdtemp


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.graph.state import IntentConstraints
from app.services.export_service import persist_export_payload
from app.services.job_service import JobStore


if __name__ == "__main__":
    demo_path = Path(mkdtemp(prefix="trip-planner-demo-"))
    store = JobStore(storage_path=demo_path / "jobs.jsonl")
    job = store.submit(
        IntentConstraints(
            user_query="Plan a low budget two-day route.",
            destination="Shanghai",
            days=2,
            budget_limit=260,
        )
    )
    if job.state is None:
        raise SystemExit(job.error or "job did not produce a state")

    payload = persist_export_payload(job.state.routing_solution, output_dir=demo_path / "exports")
    print(f"Job: {job.id} ({job.status})")
    print(f"Events: {len(job.events)}")
    print(f"Export: {payload['file_path']}")
