from __future__ import annotations

from app.core.config import settings
from app.graph.state import FinancialContext, MatrixEdge, POICandidate, WeatherConstraint
from app.services.mcp_client import MCPToolError, call_tool


class GeoFactUnavailableError(RuntimeError):
    """Raised when the Amap MCP fact layer cannot provide usable data."""


AMAP_CITY_ALIASES = {
    "shanghai": "上海",
    "beijing": "北京",
    "hangzhou": "杭州",
    "suzhou": "苏州",
    "nanjing": "南京",
    "guangzhou": "广州",
    "shenzhen": "深圳",
    "chengdu": "成都",
    "chongqing": "重庆",
    "xi'an": "西安",
    "xian": "西安",
}


def amap_mcp_enabled() -> bool:
    """Return true when Amap should be used as the geographic fact source."""
    return settings.provider_mode.lower() == "amap"


def search_poi_facts(city: str, keywords: list[str], limit: int = 10) -> list[POICandidate]:
    """Fetch POI facts from Amap MCP without making route decisions."""
    _require_amap_mode()
    try:
        payload = call_tool(
            settings.amap_mcp_poi_tool,
            {
                "city": normalize_amap_city(city),
                "keywords": keywords or ["attraction"],
                "limit": limit,
            },
        )
        pois = [POICandidate.model_validate(item) for item in payload or []]
    except (MCPToolError, ValueError, TypeError) as exc:
        raise GeoFactUnavailableError(str(exc)) from exc
    if not pois:
        raise GeoFactUnavailableError("Amap MCP returned no POI candidates")
    return pois


def resolve_hotel_fact(city: str) -> POICandidate:
    """Fetch the hotel anchor fact from Amap MCP."""
    _require_amap_mode()
    try:
        payload = call_tool(settings.amap_mcp_hotel_tool, {"city": normalize_amap_city(city)})
        return POICandidate.model_validate(payload)
    except (MCPToolError, ValueError, TypeError) as exc:
        raise GeoFactUnavailableError(str(exc)) from exc


def fetch_weather_constraint_facts(city: str) -> list[WeatherConstraint]:
    """Fetch weather-derived constraints from Amap MCP."""
    _require_amap_mode()
    try:
        payload = call_tool(settings.amap_mcp_weather_tool, {"city": normalize_amap_city(city)})
        return [WeatherConstraint.model_validate(item) for item in payload or []]
    except (MCPToolError, ValueError, TypeError) as exc:
        raise GeoFactUnavailableError(str(exc)) from exc


def build_time_dependent_matrix_facts(
    nodes: list[POICandidate],
    financial: FinancialContext,
) -> dict[str, MatrixEdge]:
    """Fetch the road-network tensor from Amap MCP."""
    _require_amap_mode()
    try:
        payload = call_tool(
            settings.amap_mcp_matrix_tool,
            {
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "financial": financial.model_dump(mode="json"),
            },
        )
        matrix = {
            key: MatrixEdge.model_validate(value)
            for key, value in (payload or {}).items()
        }
    except (MCPToolError, ValueError, TypeError) as exc:
        raise GeoFactUnavailableError(str(exc)) from exc
    if not matrix:
        raise GeoFactUnavailableError("Amap MCP returned an empty matrix")
    return matrix


def _require_amap_mode() -> None:
    if not amap_mcp_enabled():
        raise GeoFactUnavailableError("Amap MCP fact layer is not enabled")
    if not settings.mcp_http_url and not settings.mcp_allow_inprocess:
        raise GeoFactUnavailableError(
            "Amap MCP requires an external endpoint. Set TRIP_MCP_HTTP_URL to the real Amap MCP server."
        )


def normalize_amap_city(city: str) -> str:
    """Convert internal destination labels into city names Amap matches well."""
    normalized = city.strip()
    return AMAP_CITY_ALIASES.get(normalized.lower(), normalized)
