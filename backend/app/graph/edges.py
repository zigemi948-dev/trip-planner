from app.graph.state import GraphStatus, TripState


def should_retry_budget(state: TripState) -> bool:
    """Return true when a solved route should be pruned and retried."""
    return (
        state.graph_controls.current_status == GraphStatus.budget_checked
        and state.routing_solution.budget_breakdown.remaining < 0
    )


def should_interrupt_for_edit(state: TripState) -> bool:
    """Return true when a user edit should pause normal graph execution."""
    return bool(state.graph_controls.edit_trigger)
