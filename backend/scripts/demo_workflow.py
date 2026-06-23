from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
# Allow running this file directly from the repository root without installing
# the backend as a package first.
sys.path.insert(0, str(BACKEND_ROOT))

from app.graph.state import IntentConstraints
from app.graph.workflow import run_trip_workflow


if __name__ == "__main__":
    state = run_trip_workflow(
        IntentConstraints(
            user_query="Plan a balanced two-day city trip under budget.",
            destination="Shanghai",
            days=2,
            budget_limit=600,
            preferences=["museum", "food", "landmark"],
        )
    )
    print(state.routing_solution.narrative)
