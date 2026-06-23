from app.graph.state import FinancialContext


def finance_context_tool() -> FinancialContext:
    """Fallback-compatible shape for a future finance MCP tool."""
    return FinancialContext()

