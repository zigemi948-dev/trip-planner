from app.algorithms.matrix_builder import build_fallback_matrix
from app.graph.state import FinancialContext, POICandidate
from app.services.amap_service import AmapUnavailableError, build_amap_matrix, resolve_hotel, search_pois


def distance_matrix_tool(nodes: list[POICandidate], financial: FinancialContext):
    """Return Amap distance edges when configured, otherwise fallback edges."""
    try:
        return build_amap_matrix(nodes, financial)
    except AmapUnavailableError:
        return build_fallback_matrix(nodes, financial)


def poi_search_tool(city: str, keywords: list[str], limit: int = 10) -> list[POICandidate]:
    """Return Amap POI candidates for MCP-style provider calls."""
    return search_pois(city, keywords, limit=limit)


def hotel_anchor_tool(city: str) -> POICandidate:
    """Return an Amap hotel anchor for MCP-style provider calls."""
    return resolve_hotel(city)
