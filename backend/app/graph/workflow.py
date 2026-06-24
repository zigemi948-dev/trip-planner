from dataclasses import dataclass
from typing import Callable

from langgraph.graph import END, START, StateGraph

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
LANGGRAPH_WORKFLOW = None


def run_trip_workflow(intent: IntentConstraints) -> TripState:
    """Execute the deterministic Map-Compute-Reduce planning pipeline."""
    state = TripState(intent_constraints=intent)
    for _, next_state in iter_trip_workflow(intent):
        state = next_state
    return state


def iter_trip_workflow(intent: IntentConstraints):
    """Yield LangGraph runtime state after each major node for streaming clients."""
    initial_state = TripState(intent_constraints=intent)
    for update in get_langgraph_workflow().stream(initial_state, stream_mode="updates"):
        for node_name, payload in update.items():
            yield node_name, _coerce_trip_state(payload)
    return None


def get_langgraph_workflow():
    """Return the compiled LangGraph runtime graph."""
    global LANGGRAPH_WORKFLOW
    if LANGGRAPH_WORKFLOW is None:
        LANGGRAPH_WORKFLOW = build_langgraph_workflow()
    return LANGGRAPH_WORKFLOW


def build_langgraph_workflow():
    """Compile the Map-Compute-Reduce topology with LangGraph StateGraph."""
    graph = StateGraph(TripState)
    for node in MAIN_COMPUTE_NODES:
        if node.name == BUDGET_NODE.name:
            graph.add_node(node.name, _langgraph_budget_node)
        else:
            graph.add_node(node.name, _node_action(node))
    graph.add_node(BUDGET_REPAIR_NODE.name, _langgraph_budget_repair_node)
    for node in REDUCE_NODES:
        graph.add_node(node.name, _node_action(node))

    graph.add_edge(START, "map_agents")
    graph.add_edge("map_agents", "matrix_builder")
    graph.add_edge("matrix_builder", "vrp_solver")
    graph.add_edge("vrp_solver", "budget_evaluator")
    graph.add_conditional_edges(
        "budget_evaluator",
        _route_after_budget,
        {
            "repair": "budget_repair",
            "reduce": "context_compressor",
        },
    )
    graph.add_conditional_edges(
        "budget_repair",
        _route_after_budget_repair,
        {
            "retry": "matrix_builder",
            "reduce": "context_compressor",
        },
    )
    graph.add_edge("context_compressor", "planner_reduce")
    graph.add_edge("planner_reduce", END)
    return graph.compile()


def _node_action(node: WorkflowNode) -> Callable[[TripState], TripState]:
    def action(state: TripState) -> TripState:
        return _run_node(_coerce_trip_state(state), node)

    return action


def _langgraph_budget_node(state: TripState) -> TripState:
    state = _run_node(_coerce_trip_state(state), BUDGET_NODE)
    if should_retry_budget(state) and not budget_retry_exhausted(state):
        state.emit(
            "edge_taken",
            {
                "source": "budget_evaluator",
                "target": "budget_repair",
                "condition": "budget_breakdown.remaining < 0",
            },
        )
    else:
        state.emit(
            "edge_taken",
            {
                "source": "budget_evaluator",
                "target": "context_compressor",
                "condition": "budget_breakdown.remaining >= 0 or repair_exhausted",
            },
        )
    return state


def _langgraph_budget_repair_node(state: TripState) -> TripState:
    state = _coerce_trip_state(state)
    before_count = len(state.spatial_graph_data.poi_candidates)
    state = _run_node(state, BUDGET_REPAIR_NODE)
    if len(state.spatial_graph_data.poi_candidates) == before_count:
        state.emit("edge_blocked", {"source": "budget_repair", "reason": "no candidate pruned"})
        return state
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
    return state


def _route_after_budget(state: TripState) -> str:
    state = _coerce_trip_state(state)
    if should_retry_budget(state) and not budget_retry_exhausted(state):
        return "repair"
    return "reduce"


def _route_after_budget_repair(state: TripState) -> str:
    state = _coerce_trip_state(state)
    if state.graph_controls.current_status == GraphStatus.budget_repaired:
        return "retry"
    return "reduce"


def _coerce_trip_state(value) -> TripState:
    if isinstance(value, TripState):
        return value
    return TripState.model_validate(value)


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
