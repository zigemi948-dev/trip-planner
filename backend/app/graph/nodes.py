from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.agents.planner_agent import render_narrative
from app.algorithms.budget_evaluator import evaluate_budget
from app.algorithms.budget_pruner import remove_budget_candidate
from app.algorithms.observability import build_fitness_curve, compute_route_quality
from app.algorithms.vrp_solver import is_feasible_visit, solve_routes
from app.graph.state import GraphStatus, RoutingSolution, TripState
from app.services.matrix_service import build_time_dependent_matrix_with_source
from app.services.provider_adapters import provider_registry


def _run_map_agent(name: str, fn: Callable[[], Any]) -> tuple[str, Any]:
    return name, fn()


def map_agents_node(state: TripState) -> TripState:
    """Run all Map-stage agents and store their normalized outputs.

    The PRD defines this stage as parallel feature retrieval. Providers own
    fallback behavior; this node only joins their normalized outputs.
    """
    agent_calls: dict[str, Callable[[], Any]] = {
        "finance_agent": lambda: provider_registry.finance.context(state.intent_constraints),
        "hotel_agent": lambda: provider_registry.hotels.resolve_anchor(state.intent_constraints),
        "attraction_agent": lambda: provider_registry.attractions.search(state.intent_constraints),
        "weather_agent": lambda: provider_registry.weather.constraints(state.intent_constraints),
    }
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(agent_calls)) as executor:
        futures = [
            executor.submit(_run_map_agent, name, fn)
            for name, fn in agent_calls.items()
        ]
        for future in futures:
            name, value = future.result()
            results[name] = value
            payload: dict[str, Any] = {}
            if isinstance(value, list):
                payload["count"] = len(value)
            elif hasattr(value, "model_dump"):
                payload["type"] = value.__class__.__name__
            state.emit("map_agent_complete", {"agent": name, **payload})

    state.financial_context = results["finance_agent"]
    state.spatial_graph_data.hotel_anchor = results["hotel_agent"]
    state.spatial_graph_data.poi_candidates = results["attraction_agent"]
    state.spatial_graph_data.weather_constraints = results["weather_agent"]
    poi_source = "amap" if any(poi.id.startswith("amap_") for poi in state.spatial_graph_data.poi_candidates) else "local"
    local_hotel_name = f"{state.intent_constraints.destination} Central Hotel"
    hotel_source = "amap" if (
        state.spatial_graph_data.hotel_anchor and state.spatial_graph_data.hotel_anchor.name != local_hotel_name
    ) else "local"
    state.emit(
        "provider_status",
        {
            "poi_source": poi_source,
            "hotel_source": hotel_source,
            "poi_count": len(state.spatial_graph_data.poi_candidates),
        },
    )
    state.emit(
        "weather_constraints",
        {"count": len(state.spatial_graph_data.weather_constraints)},
    )
    state.graph_controls.current_status = GraphStatus.mapped
    return state


def matrix_builder_node(state: TripState) -> TripState:
    """Build the directed travel matrix used by downstream solvers."""
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        raise ValueError("hotel anchor is required before matrix build")
    nodes = [hotel, *state.spatial_graph_data.poi_candidates]
    matrix, source = build_time_dependent_matrix_with_source(nodes, state.financial_context)
    state.spatial_graph_data.time_dependent_tensor = matrix
    state.graph_controls.current_status = GraphStatus.matrix_ready
    state.emit("matrix_ready", {"edges": len(state.spatial_graph_data.time_dependent_tensor), "source": source})
    return state


def vrp_solver_node(state: TripState) -> TripState:
    """Run the route solver and emit progress-like solver events."""
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        raise ValueError("hotel anchor is required before solving")
    routes = solve_routes(
        hotel=hotel,
        pois=state.spatial_graph_data.poi_candidates,
        days=state.intent_constraints.days,
        matrix=state.spatial_graph_data.time_dependent_tensor,
        day_start=state.intent_constraints.time_window_baseline[0],
        day_end=state.intent_constraints.time_window_baseline[1],
        weather_constraints=state.spatial_graph_data.weather_constraints,
    )
    state.routing_solution.optimized_route = routes
    state.graph_controls.current_status = GraphStatus.solved
    for route in routes:
        state.emit("solver_epoch", {"day": route.day, "fitness": route.fitness_score, "algorithm": "NSGA-II"})
    state.routing_solution.fitness_curve = build_fitness_curve(routes)
    return state


def budget_evaluator_node(state: TripState) -> TripState:
    """Evaluate route cost and record budget warnings."""
    budget = evaluate_budget(
        routes=state.routing_solution.optimized_route,
        financial=state.financial_context,
        budget_limit=state.intent_constraints.budget_limit,
    )
    warnings = []
    if budget.remaining < 0:
        warnings.append("Budget limit exceeded; consider removing paid attractions or using transit.")
    for route in state.routing_solution.optimized_route:
        for stop in route.stops:
            if not is_feasible_visit(
                stop.poi,
                stop.arrival_time,
                stop.departure_time,
                state.spatial_graph_data.weather_constraints,
            ):
                warnings.append(
                    f"{stop.poi.name} may violate opening hours or weather constraints on day {route.day}."
                )
    state.routing_solution.budget_breakdown = budget
    state.routing_solution.warnings = warnings
    state.routing_solution.quality_metrics = compute_route_quality(
        state.routing_solution.optimized_route,
        budget,
    )
    state.graph_controls.current_status = GraphStatus.budget_checked
    state.emit(
        "budget_checked",
        {
            **budget.model_dump(),
            "budget_usage_ratio": state.routing_solution.quality_metrics.budget_usage_ratio,
        },
    )
    return state


def budget_repair_node(state: TripState) -> TripState:
    """Remove one low-value paid POI when the budget red line is violated."""
    if state.routing_solution.budget_breakdown.remaining >= 0:
        return state

    repaired_pois, action = remove_budget_candidate(state.spatial_graph_data.poi_candidates)
    if action is None:
        state.emit("budget_repair_skipped", {"reason": "no paid poi can be pruned"})
        return state

    state.spatial_graph_data.poi_candidates = repaired_pois
    state.routing_solution.repair_actions.append(action)
    state.graph_controls.current_status = GraphStatus.budget_repaired
    state.emit("budget_repaired", action.model_dump())
    return state


def context_compressor_node(state: TripState) -> TripState:
    """Compress route details into short assertions for LLM rendering."""
    compressed = []
    for route in state.routing_solution.optimized_route:
        for stop in route.stops:
            compressed.append(
                f"[D{route.day} {stop.arrival_time}, {stop.inbound_cost:.2f}, "
                f"{stop.inbound_mode}] -> [{stop.departure_time}, {stop.poi.name}]"
            )
    state.emit("compressed_context", {"items": compressed})
    state.graph_controls.current_status = GraphStatus.compressed
    return state


def planner_reduce_node(state: TripState) -> TripState:
    """Run the Reduce-stage planner renderer."""
    solution = RoutingSolution(
        optimized_route=state.routing_solution.optimized_route,
        budget_breakdown=state.routing_solution.budget_breakdown,
        warnings=state.routing_solution.warnings,
        repair_actions=state.routing_solution.repair_actions,
        quality_metrics=state.routing_solution.quality_metrics,
        fitness_curve=state.routing_solution.fitness_curve,
    )
    solution.narrative = render_narrative(solution)
    state.routing_solution = solution
    state.graph_controls.current_status = GraphStatus.rendered
    state.emit("rendered", {"chars": len(solution.narrative)})
    return state
