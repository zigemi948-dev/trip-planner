from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.graph.state import Coordinates, IntentConstraints, POICandidate, ReplanRequest
from app.graph.workflow import run_replan_workflow, run_trip_workflow


if __name__ == "__main__":
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a balanced two-day city trip under budget.",
            destination="Shanghai",
            days=2,
            budget_limit=900,
            preferences=["museum", "food", "landmark"],
        )
    )
    new_poi = POICandidate(
        id="poi_library",
        name="City Library",
        category="library",
        coordinates=Coordinates(lat=31.226, lng=121.471),
        fixed_cost=0,
        visit_duration_minutes=60,
        utility=6.4,
        opening_window=("09:00", "20:00"),
        indoor=True,
    )
    replanned = run_replan_workflow(ReplanRequest(state=state, day=1, new_poi=new_poi))
    print(replanned.routing_solution.narrative)
