from app.core.config import settings
from app.graph.state import IntentConstraints, WeatherConstraint
from app.services.mcp_client import MCPToolError, call_tool


def build_weather_constraints(intent: IntentConstraints) -> list[WeatherConstraint]:
    """Return weather-derived constraints for later solver filtering."""
    if settings.provider_mode.lower() == "amap":
        try:
            payload = call_tool("amap_weather_constraints", {"city": intent.destination})
            return [WeatherConstraint.model_validate(item) for item in payload or []]
        except (MCPToolError, ValueError, TypeError):
            pass

    return [
        WeatherConstraint(
            time_window=("14:00", "16:00"),
            rule="avoid_outdoor",
            block_outdoor=True,
            reason="demo_heat_or_rain_window",
        )
    ]
