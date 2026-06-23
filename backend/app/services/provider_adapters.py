from __future__ import annotations

from typing import Protocol

from app.agents.attraction_agent import search_attractions
from app.agents.finance_agent import resolve_financial_context
from app.agents.hotel_agent import resolve_hotel_anchor
from app.agents.weather_agent import build_weather_constraints
from app.core.config import settings
from app.graph.state import FinancialContext, IntentConstraints, POICandidate, WeatherConstraint


class AttractionProvider(Protocol):
    """Boundary for POI providers such as Amap MCP or local fixtures."""

    def search(self, intent: IntentConstraints) -> list[POICandidate]:
        ...


class HotelProvider(Protocol):
    """Boundary for hotel anchor providers."""

    def resolve_anchor(self, intent: IntentConstraints) -> POICandidate:
        ...


class WeatherProvider(Protocol):
    """Boundary for weather APIs that produce solver constraints."""

    def constraints(self, intent: IntentConstraints) -> list[WeatherConstraint]:
        ...


class FinanceProvider(Protocol):
    """Boundary for finance APIs such as exchange rates and city costs."""

    def context(self, intent: IntentConstraints) -> FinancialContext:
        ...


class LocalAttractionProvider:
    def search(self, intent: IntentConstraints) -> list[POICandidate]:
        return search_attractions(intent)


class LocalHotelProvider:
    def resolve_anchor(self, intent: IntentConstraints) -> POICandidate:
        return resolve_hotel_anchor(intent)


class LocalWeatherProvider:
    def constraints(self, intent: IntentConstraints) -> list[WeatherConstraint]:
        return build_weather_constraints(intent)


class LocalFinanceProvider:
    def context(self, intent: IntentConstraints) -> FinancialContext:
        return resolve_financial_context(intent)


class AmapAttractionProvider:
    """Amap MCP-backed POI provider with local fallback inside the agent."""

    def search(self, intent: IntentConstraints) -> list[POICandidate]:
        return search_attractions(intent)


class AmapHotelProvider:
    """Amap MCP-backed hotel anchor provider with local fallback inside the agent."""

    def resolve_anchor(self, intent: IntentConstraints) -> POICandidate:
        return resolve_hotel_anchor(intent)


class AmapWeatherProvider:
    """Amap MCP-backed weather provider with local fallback inside the agent."""

    def constraints(self, intent: IntentConstraints) -> list[WeatherConstraint]:
        return build_weather_constraints(intent)


class MCPFinanceProvider:
    """Finance provider routed through the MCP tool boundary."""

    def context(self, intent: IntentConstraints) -> FinancialContext:
        return resolve_financial_context(intent)


class ProviderRegistry:
    """Aggregates provider implementations used by the Map stage."""

    def __init__(
        self,
        attractions: AttractionProvider | None = None,
        hotels: HotelProvider | None = None,
        weather: WeatherProvider | None = None,
        finance: FinanceProvider | None = None,
    ) -> None:
        self.attractions = attractions or LocalAttractionProvider()
        self.hotels = hotels or LocalHotelProvider()
        self.weather = weather or LocalWeatherProvider()
        self.finance = finance or LocalFinanceProvider()


def build_provider_registry() -> ProviderRegistry:
    """Build the active provider registry from environment settings."""
    if settings.provider_mode.lower() == "amap":
        return ProviderRegistry(
            attractions=AmapAttractionProvider(),
            hotels=AmapHotelProvider(),
            weather=AmapWeatherProvider(),
            finance=MCPFinanceProvider(),
        )
    return ProviderRegistry(finance=MCPFinanceProvider())


provider_registry = build_provider_registry()
