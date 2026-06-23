from app.graph.nodes import (
    budget_evaluator_node,
    budget_repair_node,
    context_compressor_node,
    map_agents_node,
    matrix_builder_node,
    planner_reduce_node,
    vrp_solver_node,
)
from app.algorithms.vrp_solver import cheapest_insertion
from app.core.exceptions import InvalidPOIError
from app.graph.state import GraphStatus, IntentConstraints, ReplanRequest, TripState
from app.services.matrix_service import build_time_dependent_matrix


def run_trip_workflow(intent: IntentConstraints) -> TripState:
    """Execute the deterministic Map-Compute-Reduce planning pipeline."""
    state = TripState(intent_constraints=intent)
    for _, next_state in iter_trip_workflow(intent):
        state = next_state
    return state


def iter_trip_workflow(intent: IntentConstraints):
    """Yield workflow state after each major node for streaming clients."""
    state = TripState(intent_constraints=intent)
    for name, node in (
        ("map", map_agents_node),
        ("matrix", matrix_builder_node),
        ("solve", vrp_solver_node),
        ("budget", budget_evaluator_node),
    ):
        state = node(state)
        yield name, state

    repair_attempts = 0
    max_repair_attempts = len(state.spatial_graph_data.poi_candidates)
    while state.routing_solution.budget_breakdown.remaining < 0 and repair_attempts < max_repair_attempts:
        before_count = len(state.spatial_graph_data.poi_candidates)
        state = budget_repair_node(state)
        yield "budget_repair", state
        if len(state.spatial_graph_data.poi_candidates) == before_count:
            break
        state = matrix_builder_node(state)
        yield "matrix", state
        state = vrp_solver_node(state)
        yield "solve", state
        state = budget_evaluator_node(state)
        yield "budget", state
        repair_attempts += 1

    for name, node in (
        ("compress", context_compressor_node),
        ("render", planner_reduce_node),
    ):
        state = node(state)
        yield name, state
    return state


def _repair_budget_until_feasible(state: TripState) -> TripState:
    """Apply the PRD budget red-line loop until the route is feasible or stuck."""
    repair_attempts = 0
    max_repair_attempts = len(state.spatial_graph_data.poi_candidates)
    while state.routing_solution.budget_breakdown.remaining < 0 and repair_attempts < max_repair_attempts:
        before_count = len(state.spatial_graph_data.poi_candidates)
        state = budget_repair_node(state)
        if len(state.spatial_graph_data.poi_candidates) == before_count:
            break
        state = matrix_builder_node(state)
        state = vrp_solver_node(state)
        state = budget_evaluator_node(state)
        repair_attempts += 1
    return state


def run_replan_workflow(request: ReplanRequest) -> TripState:
    """Repair one day route after a user inserts a POI."""
    state = request.state
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        raise InvalidPOIError("hotel anchor is required before replanning")

    target_route = next(
        (route for route in state.routing_solution.optimized_route if route.day == request.day),
        None,
    )
    if target_route is None:
        raise InvalidPOIError(f"day {request.day} route does not exist")

    if request.new_poi.id in {stop.poi.id for stop in target_route.stops}:
        raise InvalidPOIError(f"{request.new_poi.name} is already scheduled on day {request.day}")

    if request.new_poi.id not in {poi.id for poi in state.spatial_graph_data.poi_candidates}:
        state.spatial_graph_data.poi_candidates.append(request.new_poi)

    matrix_nodes = [hotel, *state.spatial_graph_data.poi_candidates]
    state.spatial_graph_data.time_dependent_tensor = build_time_dependent_matrix(
        matrix_nodes,
        state.financial_context,
    )

    repaired = cheapest_insertion(
        target_route,
        request.new_poi,
        hotel,
        state.spatial_graph_data.time_dependent_tensor,
        day_start=state.intent_constraints.time_window_baseline[0],
        weather_constraints=state.spatial_graph_data.weather_constraints,
    )
    repaired.day = request.day

    state.routing_solution.optimized_route = [
        repaired if route.day == request.day else route
        for route in state.routing_solution.optimized_route
    ]
    state.graph_controls.current_status = GraphStatus.replanned
    state.emit("replanned", {"day": request.day, "inserted": request.new_poi.id})

    state = budget_evaluator_node(state)
    state = _repair_budget_until_feasible(state)
    state = context_compressor_node(state)
    state = planner_reduce_node(state)
    return state
