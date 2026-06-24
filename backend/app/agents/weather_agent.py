from app.graph.state import IntentConstraints, WeatherConstraint
from app.services.geo_fact_service import GeoFactUnavailableError, fetch_weather_constraint_facts


def build_weather_constraints(intent: IntentConstraints) -> list[WeatherConstraint]:
    """Return weather-derived constraints for later solver filtering."""
    try:
        return fetch_weather_constraint_facts(intent.destination)
    except GeoFactUnavailableError:
        pass

    return [
        WeatherConstraint(
            time_window=("14:00", "16:00"),
            rule="avoid_outdoor",
            block_outdoor=True,
            reason="demo_heat_or_rain_window",
        )
    ]
