from datetime import date, timedelta

from app.graph.state import DailyWeatherForecast, IntentConstraints, WeatherConstraint, WeatherReport
from app.services.geo_fact_service import (
    GeoFactUnavailableError,
    fetch_weather_constraint_facts,
    fetch_weather_forecast_facts,
)


def build_weather_report(intent: IntentConstraints) -> WeatherReport:
    """Return solver constraints and user-facing daily forecasts."""
    constraints = build_weather_constraints(intent)
    forecasts = build_weather_forecast(intent)
    return WeatherReport(constraints=constraints, forecasts=forecasts)


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


def build_weather_forecast(intent: IntentConstraints) -> list[DailyWeatherForecast]:
    """Return one weather summary per itinerary day."""
    try:
        forecasts = fetch_weather_forecast_facts(intent.destination, intent.days)
        if forecasts:
            return _pad_forecasts(forecasts, intent.days)
    except GeoFactUnavailableError:
        pass

    return _fallback_forecast(intent.days)


def _fallback_forecast(days: int) -> list[DailyWeatherForecast]:
    today = date.today()
    weather_cycle = [
        ("Partly cloudy", 22.0, 29.0, "Indoor/outdoor balance is comfortable."),
        ("Cloudy", 21.0, 28.0, "Good for regular sightseeing."),
        ("Light rain", 20.0, 26.0, "Prefer indoor stops and carry rain gear."),
    ]
    forecasts: list[DailyWeatherForecast] = []
    for day in range(1, days + 1):
        weather, temp_min, temp_max, advisory = weather_cycle[(day - 1) % len(weather_cycle)]
        forecasts.append(
            DailyWeatherForecast(
                day=day,
                date=(today + timedelta(days=day - 1)).isoformat(),
                weather=weather,
                temperature_min=temp_min,
                temperature_max=temp_max,
                wind="light breeze",
                advisory=advisory,
                source="fallback",
            )
        )
    return forecasts


def _pad_forecasts(forecasts: list[DailyWeatherForecast], days: int) -> list[DailyWeatherForecast]:
    if len(forecasts) >= days:
        return [forecast.model_copy(update={"day": index}) for index, forecast in enumerate(forecasts[:days], start=1)]
    padded = [forecast.model_copy(update={"day": index}) for index, forecast in enumerate(forecasts, start=1)]
    fallback = _fallback_forecast(days)
    for index in range(len(padded), days):
        padded.append(fallback[index])
    return padded
