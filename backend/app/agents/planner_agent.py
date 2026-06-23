from app.graph.state import RoutingSolution
from app.services.llm_service import LLMUnavailableError, complete_text, llm_is_enabled


def render_narrative(solution: RoutingSolution) -> str:
    """Render the compressed route solution into human-readable text."""
    if llm_is_enabled():
        try:
            return _render_with_llm(solution)
        except LLMUnavailableError:
            pass

    return _render_with_template(solution)


def _render_with_template(solution: RoutingSolution) -> str:
    lines: list[str] = []
    for route in solution.optimized_route:
        stop_names = " -> ".join(stop.poi.name for stop in route.stops)
        lines.append(
            f"Day {route.day}: {stop_names}. "
            f"Estimated active time {route.total_minutes} minutes, route cost {route.total_cost:.2f}."
        )

    budget = solution.budget_breakdown
    lines.append(
        f"Budget total {budget.total_cost:.2f}/{budget.budget_limit:.2f}, "
        f"remaining {budget.remaining:.2f}."
    )
    if solution.warnings:
        lines.append("Warnings: " + "; ".join(solution.warnings))
    return "\n".join(lines)


def _render_with_llm(solution: RoutingSolution) -> str:
    route_lines = []
    for route in solution.optimized_route:
        stops = [
            f"{stop.arrival_time}-{stop.departure_time} {stop.poi.name} "
            f"({stop.inbound_mode}, cost {stop.inbound_cost:.2f})"
            for stop in route.stops
        ]
        route_lines.append(f"Day {route.day}: " + " -> ".join(stops))

    budget = solution.budget_breakdown
    prompt = "\n".join(
        [
            "Create a concise Chinese travel itinerary narrative from verified solver output.",
            "Do not change stop order, times, costs, or warnings.",
            *route_lines,
            (
                f"Budget: total {budget.total_cost:.2f}, limit {budget.budget_limit:.2f}, "
                f"remaining {budget.remaining:.2f}."
            ),
            "Warnings: " + ("; ".join(solution.warnings) if solution.warnings else "None"),
        ]
    )
    return complete_text(
        "You are a travel planner renderer. You only explain verified structured route data; "
        "you never recalculate geography, time, or budget.",
        prompt,
        temperature=0.4,
    )
