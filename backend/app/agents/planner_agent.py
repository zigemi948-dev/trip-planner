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
        weather = _weather_line(solution, route.day)
        hotel = _hotel_line(solution, route.day)
        lines.append(
            f"Day {route.day}: {stop_names}. "
            f"Estimated active time {route.total_minutes} minutes, route cost {route.total_cost:.2f}. "
            f"{weather} {hotel} {_cost_line(solution, route.day)}"
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
        route_lines.append(
            f"Day {route.day}: "
            + " -> ".join(stops)
            + f" | {_weather_line(solution, route.day)} | {_hotel_line(solution, route.day)} | {_cost_line(solution, route.day)}"
        )

    budget = solution.budget_breakdown
    prompt = "\n".join(
        [
            "Create a concise Chinese travel itinerary narrative from verified solver output.",
            "Do not change stop order, times, costs, or warnings.",
            "Include each day's weather and hotel stay exactly as provided.",
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


def _weather_line(solution: RoutingSolution, day: int) -> str:
    forecast = next((item for item in solution.daily_weather if item.day == day), None)
    if forecast is None:
        return "Weather: not available."
    temp = ""
    if forecast.temperature_min is not None and forecast.temperature_max is not None:
        temp = f", {forecast.temperature_min:.0f}-{forecast.temperature_max:.0f}C"
    elif forecast.temperature_max is not None:
        temp = f", {forecast.temperature_max:.0f}C"
    advisory = f" Advisory: {forecast.advisory}" if forecast.advisory else ""
    return f"Weather: {forecast.weather or 'unknown'}{temp}.{advisory}"


def _hotel_line(solution: RoutingSolution, day: int) -> str:
    stay = next((item for item in solution.hotel_stays if item.day == day), None)
    if stay is None:
        return "Hotel: not assigned."
    return (
        f"Hotel: {stay.hotel.name}, check-in {stay.check_in_time}, "
        f"daily checkout/departure {stay.check_out_time}. {stay.note}"
    )


def _cost_line(solution: RoutingSolution, day: int) -> str:
    cost = next((item for item in solution.daily_costs if item.day == day), None)
    if cost is None:
        return "Daily cost: not available."
    return (
        f"Daily cost: total {cost.total_cost:.2f} "
        f"(hotel {cost.accommodation_cost:.2f}, tickets {cost.ticket_cost:.2f}, "
        f"food {cost.food_cost:.2f}, transport {cost.transport_cost:.2f})."
    )
