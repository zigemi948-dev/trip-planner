from app.graph.state import IntentConstraints, WeatherConstraint


def build_weather_constraints(intent: IntentConstraints) -> list[WeatherConstraint]:
    """Return weather-derived constraints for later solver filtering."""
    return [
        WeatherConstraint(
            time_window=("14:00", "16:00"),
            rule="avoid_outdoor",
            block_outdoor=True,
            reason="demo_heat_or_rain_window",
        )
    ]
