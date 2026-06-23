from app.core.config import settings
from app.graph.state import Coordinates, IntentConstraints, POICandidate
from app.services.mcp_client import MCPToolError, call_tool


DEMO_POIS = [
    ("poi_museum", "City Museum", "museum", 31.2304, 121.4737, 40, 120, 9.0, True),
    ("poi_garden", "Classical Garden", "garden", 31.2272, 121.4896, 35, 90, 8.2, False),
    ("poi_tower", "Sky Tower", "landmark", 31.2397, 121.4998, 120, 100, 8.8, True),
    ("poi_market", "Old Street Market", "food", 31.2247, 121.4809, 20, 80, 7.2, False),
    ("poi_gallery", "Modern Art Gallery", "gallery", 31.2185, 121.4672, 60, 110, 7.9, True),
]


def search_attractions(intent: IntentConstraints) -> list[POICandidate]:
    """Return candidate POIs for the requested destination.

    Amap mode uses the MCP tool boundary. The deterministic demo source remains
    the local fallback when the remote provider is unavailable.
    """
    if settings.provider_mode.lower() == "amap":
        try:
            payload = call_tool(
                "amap_poi_search",
                {
                    "city": intent.destination,
                    "keywords": intent.preferences or ["attraction"],
                    "limit": 10,
                },
            )
            return [POICandidate.model_validate(item) for item in payload or []]
        except (MCPToolError, ValueError, TypeError):
            pass

    return [
        POICandidate(
            id=poi_id,
            name=name,
            category=category,
            coordinates=Coordinates(lat=lat, lng=lng),
            fixed_cost=cost,
            visit_duration_minutes=duration,
            utility=utility,
            indoor=indoor,
        )
        for poi_id, name, category, lat, lng, cost, duration, utility, indoor in DEMO_POIS
    ]
