from __future__ import annotations

from app.graph.state import BudgetBreakdown, DayRoute, FitnessPoint, RouteQualityMetrics


def build_fitness_curve(routes: list[DayRoute]) -> list[FitnessPoint]:
    """Create a deterministic fitness curve from day-level route scores."""
    curve: list[FitnessPoint] = []
    running_score = 0.0
    for index, route in enumerate(routes, start=1):
        running_score += route.fitness_score
        curve.append(
            FitnessPoint(
                epoch=index,
                label=f"Day {route.day}",
                score=round(running_score / index, 3),
            )
        )
    return curve


def compute_route_quality(
    routes: list[DayRoute],
    budget: BudgetBreakdown,
) -> RouteQualityMetrics:
    """Aggregate route metrics for monitoring and frontend dashboards."""
    stops = [stop for route in routes for stop in route.stops]
    mode_share: dict[str, int] = {}
    for stop in stops:
        mode = stop.inbound_mode.value if stop.inbound_mode else "Unknown"
        mode_share[mode] = mode_share.get(mode, 0) + 1

    average_fitness = 0.0
    if routes:
        average_fitness = sum(route.fitness_score for route in routes) / len(routes)

    budget_usage_ratio = 0.0
    if budget.budget_limit > 0:
        budget_usage_ratio = budget.total_cost / budget.budget_limit

    return RouteQualityMetrics(
        total_stops=len(stops),
        total_distance_km=round(sum(stop.inbound_distance_km for stop in stops), 2),
        total_minutes=sum(route.total_minutes for route in routes),
        total_transport_cost=round(sum(stop.inbound_cost for stop in stops), 2),
        budget_usage_ratio=round(budget_usage_ratio, 3),
        average_fitness=round(average_fitness, 3),
        mode_share=mode_share,
    )
