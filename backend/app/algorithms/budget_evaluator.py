from __future__ import annotations

from app.graph.state import BudgetBreakdown, DayRoute, FinancialContext


def evaluate_budget(
    routes: list[DayRoute],
    financial: FinancialContext,
    budget_limit: float,
) -> BudgetBreakdown:
    """Aggregate fixed attraction, transport, and meal costs for all days."""
    fixed_cost = sum(stop.poi.fixed_cost for route in routes for stop in route.stops)
    transport_cost = sum(stop.inbound_cost for route in routes for stop in route.stops)
    meal_count = len(routes) * 2
    food_cost = meal_count * financial.avg_meal_cost
    total = fixed_cost + transport_cost + food_cost

    return BudgetBreakdown(
        fixed_cost=round(fixed_cost, 2),
        transport_cost=round(transport_cost, 2),
        food_cost=round(food_cost, 2),
        total_cost=round(total, 2),
        budget_limit=budget_limit,
        remaining=round(budget_limit - total, 2),
    )
