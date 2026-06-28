from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any, Callable

from app.agents.planner_agent import render_narrative
from app.algorithms.budget_evaluator import build_daily_costs, evaluate_budget
from app.algorithms.budget_pruner import remove_budget_candidate
from app.algorithms.observability import build_fitness_curve, compute_route_quality
from app.algorithms.vrp_solver import is_feasible_visit, solve_routes
from app.graph.state import GraphStatus, HotelStay, RoutingSolution, TripState, WeatherReport
from app.graph.state import FinancialContext, POICandidate
from app.core.config import settings
from app.services.matrix_service import build_time_dependent_matrix_with_source
from app.services.provider_adapters import provider_registry

logger = logging.getLogger(__name__)


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
        "weather_agent": lambda: provider_registry.weather.report(state.intent_constraints),
    }
    results: dict[str, Any] = {}
    fallback_warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=len(agent_calls)) as executor:
        futures = [
            executor.submit(_run_map_agent, name, fn)
            for name, fn in agent_calls.items()
        ]
        for future in futures:
            name, value = future.result()
            results[name] = value
            payload: dict[str, Any] = {}
            if isinstance(value, WeatherReport):
                payload["constraints"] = len(value.constraints)
                payload["forecasts"] = len(value.forecasts)
            elif isinstance(value, list):
                payload["count"] = len(value)
            elif hasattr(value, "model_dump"):
                payload["type"] = value.__class__.__name__
            state.emit("map_agent_complete", {"agent": name, **payload})

    state.financial_context = results["finance_agent"]
    state.spatial_graph_data.hotel_anchor = results["hotel_agent"]
    state.spatial_graph_data.poi_candidates = results["attraction_agent"]
    state.financial_context, state.spatial_graph_data.poi_candidates = _apply_market_price_context(
        state.financial_context,
        state.spatial_graph_data.hotel_anchor,
        state.spatial_graph_data.poi_candidates,
    )
    weather_report: WeatherReport = results["weather_agent"]
    state.spatial_graph_data.weather_constraints = weather_report.constraints
    state.spatial_graph_data.weather_forecast = weather_report.forecasts
    poi_source = "amap" if any(poi.id.startswith("amap_") for poi in state.spatial_graph_data.poi_candidates) else "local"
    local_hotel_name = f"{state.intent_constraints.destination} Central Hotel"
    hotel_source = "amap" if (
        state.spatial_graph_data.hotel_anchor and state.spatial_graph_data.hotel_anchor.name != local_hotel_name
    ) else "local"

    # Detect fallback conditions and emit warnings
    if poi_source == "local":
        fallback_warnings.append(
            "Amap MCP POI search failed; fell back to local demo attractions. "
            "Check network / API key / Amap MCP endpoint."
        )
        logger.warning(f"attraction_agent fell back to local demo data (provider_mode={settings.provider_mode})")
    if hotel_source == "local" and poi_source == "local":
        fallback_warnings.append(
            "Amap MCP hotel search failed; fell back to default hotel anchor. "
            "Check network / API key / Amap MCP endpoint."
        )
        logger.warning(f"hotel_agent fell back to default hotel (provider_mode={settings.provider_mode})")
    if state.spatial_graph_data.weather_forecast and state.spatial_graph_data.weather_forecast[0].source == "fallback":
        fallback_warnings.append(
            "Amap MCP weather service unavailable; used fallback weather data."
        )
        logger.warning(f"weather_agent fell back to fallback forecast (provider_mode={settings.provider_mode})")

    state.emit(
        "provider_status",
        {
            "poi_source": poi_source,
            "hotel_source": hotel_source,
            "poi_count": len(state.spatial_graph_data.poi_candidates),
            "fallback": len(fallback_warnings) > 0,
        },
    )
    if fallback_warnings:
        state.routing_solution.warnings.extend(fallback_warnings)
        state.emit("fallback_warning", {"warnings": fallback_warnings})
    state.emit(
        "weather_constraints",
        {
            "count": len(state.spatial_graph_data.weather_constraints),
            "forecast_days": len(state.spatial_graph_data.weather_forecast),
        },
    )
    state.graph_controls.current_status = GraphStatus.mapped
    return state


def matrix_builder_node(state: TripState) -> TripState:
    """Build the directed travel matrix used by downstream solvers."""
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        raise ValueError("hotel anchor is required before matrix build")
    nodes = [hotel, *state.spatial_graph_data.poi_candidates]
    matrix, source = build_time_dependent_matrix_with_source(
        nodes,
        state.financial_context,
        state.intent_constraints.destination,
    )
    state.spatial_graph_data.time_dependent_tensor = matrix
    state.graph_controls.current_status = GraphStatus.matrix_ready
    state.emit("matrix_ready", {"edges": len(state.spatial_graph_data.time_dependent_tensor), "source": source})
    return state


def vrp_solver_node(state: TripState) -> TripState:
    """Run the route solver and emit progress-like solver events."""
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        raise ValueError("hotel anchor is required before solving")

    def emit_solver_progress(day: int, epoch: int, fitness: float) -> None:
        state.emit(
            "solver_epoch",
            {
                "day": day,
                "epoch": epoch,
                "fitness": fitness,
                "algorithm": "NSGA-II",
            },
        )

    routes = solve_routes(
        hotel=hotel,
        pois=state.spatial_graph_data.poi_candidates,
        days=state.intent_constraints.days,
        matrix=state.spatial_graph_data.time_dependent_tensor,
        day_start=state.intent_constraints.time_window_baseline[0],
        day_end=state.intent_constraints.time_window_baseline[1],
        weather_constraints=state.spatial_graph_data.weather_constraints,
        budget_limit=state.intent_constraints.budget_limit,
        progress_callback=emit_solver_progress,
    )
    state.routing_solution.optimized_route = routes
    state.graph_controls.current_status = GraphStatus.solved
    state.routing_solution.fitness_curve = build_fitness_curve(routes)
    return state


def budget_evaluator_node(state: TripState) -> TripState:
    """Evaluate route cost and record budget warnings."""
    budget = evaluate_budget(
        routes=state.routing_solution.optimized_route,
        financial=state.financial_context,
        budget_limit=state.intent_constraints.budget_limit,
    )
    warnings = list(state.routing_solution.warnings)
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
    daily_costs = build_daily_costs(state.routing_solution.optimized_route, state.financial_context)
    state.routing_solution.daily_costs = daily_costs
    costs_by_day = {item.day: item for item in daily_costs}
    state.routing_solution.optimized_route = [
        route.model_copy(update={"cost_breakdown": costs_by_day.get(route.day)})
        for route in state.routing_solution.optimized_route
    ]
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
        daily_costs=state.routing_solution.daily_costs,
        hotel_anchor=state.spatial_graph_data.hotel_anchor,
        hotel_stays=_build_hotel_stays(state),
        daily_weather=state.spatial_graph_data.weather_forecast,
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


def _apply_market_price_context(
    financial_context: FinancialContext,
    hotel: POICandidate | None,
    pois: list[POICandidate],
) -> tuple[FinancialContext, list[POICandidate]]:
    """Use Amap POI prices to tune hotel and meal assumptions without double-counting food stops."""
    updates = {}
    if hotel is not None and hotel.fixed_cost > 0:
        updates["avg_hotel_nightly_cost"] = hotel.fixed_cost

    meal_costs = [poi.fixed_cost for poi in pois if poi.category == "food" and poi.fixed_cost > 0]
    normalized_pois = pois
    if meal_costs:
        updates["avg_meal_cost"] = sum(meal_costs) / len(meal_costs)
        normalized_pois = [
            poi.model_copy(update={"fixed_cost": 0.0}) if poi.category == "food" else poi
            for poi in pois
        ]

    if updates:
        financial_context = financial_context.model_copy(update=updates)
    return financial_context, normalized_pois


def _build_hotel_stays(state: TripState) -> list[HotelStay]:
    hotel = state.spatial_graph_data.hotel_anchor
    if hotel is None:
        return []
    day_start = state.intent_constraints.time_window_baseline[0]
    day_end = state.intent_constraints.time_window_baseline[1]
    stays: list[HotelStay] = []
    for route in state.routing_solution.optimized_route:
        note = "Check out for the day route, then return after the last stop."
        if route.stops:
            note = f"Return after {route.stops[-1].departure_time} from {route.stops[-1].poi.name}."
        stays.append(
            HotelStay(
                day=route.day,
                hotel=hotel,
                check_in_time="Previous night" if route.day > 1 else f"Before {day_start}",
                check_out_time=day_start,
                note=f"{note} Planned rest window starts around {day_end}.",
            )
        )
    return stays
