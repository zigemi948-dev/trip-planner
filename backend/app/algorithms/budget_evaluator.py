from __future__ import annotations

from app.graph.state import BudgetBreakdown, DayCostBreakdown, DayRoute, FinancialContext


def build_daily_costs(routes: list[DayRoute], financial: FinancialContext) -> list[DayCostBreakdown]:
    """Calculate per-day ticket, transport, food, and accommodation cost."""
    daily_costs: list[DayCostBreakdown] = []
    last_day = max((route.day for route in routes), default=0)
    for route in routes:
        ticket_cost = sum(stop.poi.fixed_cost for stop in route.stops)
        transport_cost = sum(stop.inbound_cost for stop in route.stops)
        food_cost = 2 * financial.avg_meal_cost
        accommodation_cost = financial.avg_hotel_nightly_cost if route.day < last_day else 0.0
        total = ticket_cost + transport_cost + food_cost + accommodation_cost
        daily_costs.append(
            DayCostBreakdown(
                day=route.day,
                ticket_cost=round(ticket_cost, 2),
                transport_cost=round(transport_cost, 2),
                food_cost=round(food_cost, 2),
                accommodation_cost=round(accommodation_cost, 2),
                total_cost=round(total, 2),
            )
        )
    return daily_costs


def evaluate_budget(
    routes: list[DayRoute],
    financial: FinancialContext,
    budget_limit: float,
) -> BudgetBreakdown:
    """Aggregate fixed attraction, transport, and meal costs for all days."""
    daily_costs = build_daily_costs(routes, financial)
    fixed_cost = sum(day.ticket_cost for day in daily_costs)
    transport_cost = sum(day.transport_cost for day in daily_costs)
    food_cost = sum(day.food_cost for day in daily_costs)
    accommodation_cost = sum(day.accommodation_cost for day in daily_costs)
    total = fixed_cost + transport_cost + food_cost + accommodation_cost

    return BudgetBreakdown(
        fixed_cost=round(fixed_cost, 2),
        transport_cost=round(transport_cost, 2),
        food_cost=round(food_cost, 2),
        accommodation_cost=round(accommodation_cost, 2),
        total_cost=round(total, 2),
        budget_limit=budget_limit,
        remaining=round(budget_limit - total, 2),
    )
