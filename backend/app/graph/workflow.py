from dataclasses import dataclass
from typing import Callable

from app.graph.edges import budget_retry_exhausted, should_retry_budget
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


@dataclass(frozen=True)
class WorkflowNode:
    """Executable graph node metadata."""

    name: str
    phase: str
    handler: Callable[[TripState], TripState]


MAIN_COMPUTE_NODES = (
    WorkflowNode("map_agents", "Map", map_agents_node),
    WorkflowNode("matrix_builder", "Compute", matrix_builder_node),
    WorkflowNode("vrp_solver", "Compute", vrp_solver_node),
    WorkflowNode("budget_evaluator", "Compute", budget_evaluator_node),
)

REDUCE_NODES = (
    WorkflowNode("context_compressor", "Reduce", context_compressor_node),
    WorkflowNode("planner_reduce", "Reduce", planner_reduce_node),
)

BUDGET_REPAIR_NODE = WorkflowNode("budget_repair", "Compute", budget_repair_node)
MATRIX_NODE = WorkflowNode("matrix_builder", "Compute", matrix_builder_node)
SOLVER_NODE = WorkflowNode("vrp_solver", "Compute", vrp_solver_node)
BUDGET_NODE = WorkflowNode("budget_evaluator", "Compute", budget_evaluator_node)


def run_trip_workflow(intent: IntentConstraints) -> TripState:
    """Execute the deterministic Map-Compute-Reduce planning pipeline."""
    state = TripState(intent_constraints=intent)
    for _, next_state in iter_trip_workflow(intent):
        state = next_state
    return state


def iter_trip_workflow(intent: IntentConstraints):
    """Yield workflow state after each major node for streaming clients."""
    state = TripState(intent_constraints=intent)
    for node in MAIN_COMPUTE_NODES:
        state = _run_node(state, node)
        yield node.name, state

    while should_retry_budget(state) and not budget_retry_exhausted(state):
        before_count = len(state.spatial_graph_data.poi_candidates)
        state.emit(
            "edge_taken",
            {
                "source": "budget_evaluator",
                "target": "budget_repair",
                "condition": "budget_breakdown.remaining < 0",
            },
        )
        state = _run_node(state, BUDGET_REPAIR_NODE)
        yield BUDGET_REPAIR_NODE.name, state
        if len(state.spatial_graph_data.poi_candidates) == before_count:
            state.emit("edge_blocked", {"source": "budget_repair", "reason": "no candidate pruned"})
            break
        state.graph_controls.repair_attempts += 1
        state.emit(
            "edge_taken",
            {
                "source": "budget_repair",
                "target": "matrix_builder",
                "condition": "candidate_pruned",
                "repair_attempt": state.graph_controls.repair_attempts,
            },
        )
        for node in (MATRIX_NODE, SOLVER_NODE, BUDGET_NODE):
            state = _run_node(state, node)
            yield node.name, state

    state.emit(
        "edge_taken",
        {
            "source": "budget_evaluator",
            "target": "context_compressor",
            "condition": "budget_breakdown.remaining >= 0 or repair_exhausted",
        },
    )
    for node in REDUCE_NODES:
        state = _run_node(state, node)
        yield node.name, state
    return state


def _run_node(state: TripState, node: WorkflowNode) -> TripState:
    """Execute one graph node and annotate workflow observability fields."""
    state.graph_controls.current_node = node.name
    state.graph_controls.current_phase = node.phase
    state.emit("node_started", {"node": node.name, "phase": node.phase})
    state = node.handler(state)
    state.graph_controls.current_node = node.name
    state.graph_controls.current_phase = node.phase
    state.emit(
        "node_completed",
        {
            "node": node.name,
            "phase": node.phase,
            "status": state.graph_controls.current_status.value,
        },
    )
    return state


def _repair_budget_until_feasible(state: TripState) -> TripState:
    """Apply the PRD budget red-line loop until the route is feasible or stuck."""
    while should_retry_budget(state) and not budget_retry_exhausted(state):
        before_count = len(state.spatial_graph_data.poi_candidates)
        state = _run_node(state, BUDGET_REPAIR_NODE)
        if len(state.spatial_graph_data.poi_candidates) == before_count:
            break
        state.graph_controls.repair_attempts += 1
        for node in (MATRIX_NODE, SOLVER_NODE, BUDGET_NODE):
            state = _run_node(state, node)
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
        day_end=state.intent_constraints.time_window_baseline[1],
        weather_constraints=state.spatial_graph_data.weather_constraints,
    )
    repaired.day = request.day

    state.routing_solution.optimized_route = [
        repaired if route.day == request.day else route
        for route in state.routing_solution.optimized_route
    ]
    state.graph_controls.current_status = GraphStatus.replanned
    state.emit("replanned", {"day": request.day, "inserted": request.new_poi.id})

    state = _run_node(state, BUDGET_NODE)
    state = _repair_budget_until_feasible(state)
    for node in REDUCE_NODES:
        state = _run_node(state, node)
    return state
