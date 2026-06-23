from app.graph.state import FinancialContext, IntentConstraints
from app.services.mcp_client import MCPToolError, call_tool


def resolve_financial_context(intent: IntentConstraints) -> FinancialContext:
    """Return city financial assumptions for budget calculations."""
    try:
        payload = call_tool("finance_context", {})
        return FinancialContext.model_validate(payload)
    except (MCPToolError, ValueError, TypeError):
        pass

    return FinancialContext(
        currency="CNY",
        exchange_rate=1.0,
        base_transit_fare=4.0,
        driving_rate_per_km=2.6,
        avg_meal_cost=45.0,
    )
