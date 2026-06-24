from app.graph.state import (
    GraphStatus,
    TripState,
    WorkflowTopology,
    WorkflowTopologyEdge,
    WorkflowTopologyNode,
)


TOPOLOGY_NODES = [
    WorkflowTopologyNode(
        name="map_agents",
        phase="Map",
        description="Run attraction, hotel, weather, and finance agents to normalize external features.",
    ),
    WorkflowTopologyNode(
        name="matrix_builder",
        phase="Compute",
        description="Build the time-dependent directed travel tensor.",
    ),
    WorkflowTopologyNode(
        name="vrp_solver",
        phase="Compute",
        description="Solve capacity-clustered TD-VRPTW day routes with NSGA-II.",
    ),
    WorkflowTopologyNode(
        name="budget_evaluator",
        phase="Compute",
        description="Evaluate financial red-line constraints on the directed route graph.",
    ),
    WorkflowTopologyNode(
        name="budget_repair",
        phase="Compute",
        description="Prune low-value paid candidates and loop back into matrix and solver nodes.",
    ),
    WorkflowTopologyNode(
        name="context_compressor",
        phase="Reduce",
        description="Compress high-dimensional route data into concise temporal assertions.",
    ),
    WorkflowTopologyNode(
        name="planner_reduce",
        phase="Reduce",
        description="Render verified route and budget facts into user-facing narrative.",
    ),
]

TOPOLOGY_EDGES = [
    WorkflowTopologyEdge(source="map_agents", target="matrix_builder"),
    WorkflowTopologyEdge(source="matrix_builder", target="vrp_solver"),
    WorkflowTopologyEdge(source="vrp_solver", target="budget_evaluator"),
    WorkflowTopologyEdge(
        source="budget_evaluator",
        target="budget_repair",
        condition="budget_breakdown.remaining < 0",
    ),
    WorkflowTopologyEdge(
        source="budget_repair",
        target="matrix_builder",
        condition="candidate_pruned",
    ),
    WorkflowTopologyEdge(
        source="budget_evaluator",
        target="context_compressor",
        condition="budget_breakdown.remaining >= 0 or repair_exhausted",
    ),
    WorkflowTopologyEdge(source="context_compressor", target="planner_reduce"),
]


def workflow_topology() -> WorkflowTopology:
    """Return the deterministic Map-Compute-Reduce graph shape."""
    return WorkflowTopology(nodes=TOPOLOGY_NODES, edges=TOPOLOGY_EDGES)


def should_retry_budget(state: TripState) -> bool:
    """Return true when a solved route should be pruned and retried."""
    return (
        state.graph_controls.current_status == GraphStatus.budget_checked
        and state.routing_solution.budget_breakdown.remaining < 0
    )


def budget_retry_exhausted(state: TripState) -> bool:
    """Return true when further budget repair cannot remove candidates."""
    return not any(poi.fixed_cost > 0 for poi in state.spatial_graph_data.poi_candidates)


def should_interrupt_for_edit(state: TripState) -> bool:
    """Return true when a user edit should pause normal graph execution."""
    return bool(state.graph_controls.edit_trigger)
