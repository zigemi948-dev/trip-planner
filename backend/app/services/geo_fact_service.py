from __future__ import annotations

from app.core.config import settings
from app.graph.state import Coordinates, DailyWeatherForecast, FinancialContext, MatrixEdge, POICandidate, WeatherConstraint
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

OFFICIAL_AMAP_TEXT_SEARCH_TOOL = "maps_text_search"
OFFICIAL_AMAP_SEARCH_DETAIL_TOOL = "maps_search_detail"
OFFICIAL_AMAP_GEO_TOOL = "maps_geo"
OFFICIAL_AMAP_WEATHER_TOOL = "maps_weather"

OFFICIAL_KEYWORDS = {
    "museum": ["museum"],
    "food": ["restaurant", "food"],
    "landmark": ["attraction", "landmark"],
    "gallery": ["gallery", "art museum"],
    "garden": ["park", "garden"],
    "library": ["library"],
    "shopping": ["shopping mall", "mall"],
    "family": ["theme park", "kids"],
    "hotel": ["hotel"],
}


def amap_mcp_enabled() -> bool:
    """Return true when Amap should be used as the geographic fact source."""
    return settings.provider_mode.lower() == "amap"


def search_poi_facts(city: str, keywords: list[str], limit: int = 10) -> list[POICandidate]:
    """Fetch POI facts from Amap MCP without making route decisions."""
    _require_amap_mode()
    errors: list[str] = []
    try:
        payload = call_tool(
            settings.amap_mcp_poi_tool,
            {
                "city": normalize_amap_city(city),
                "keywords": keywords or ["attraction"],
                "limit": limit,
            },
        )
        pois = _coerce_poi_candidates(payload, fallback_category=(keywords or ["attraction"])[0])
        if pois:
            return pois[:limit]
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    try:
        pois = _search_official_amap_pois(city, keywords or ["attraction"], limit)
        if pois:
            return pois
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    detail = f": {'; '.join(errors)}" if errors else ""
    raise GeoFactUnavailableError(f"Amap MCP returned no POI candidates{detail}")


def resolve_hotel_fact(city: str) -> POICandidate:
    """Fetch the hotel anchor fact from Amap MCP."""
    _require_amap_mode()
    errors: list[str] = []
    try:
        payload = call_tool(settings.amap_mcp_hotel_tool, {"city": normalize_amap_city(city)})
        return POICandidate.model_validate(payload)
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    try:
        hotels = _search_official_amap_pois(city, ["hotel"], limit=1)
        if hotels:
            hotel = hotels[0]
            return hotel.model_copy(
                update={
                    "id": "hotel_anchor",
                    "category": "hotel",
                    "fixed_cost": 0.0,
                    "visit_duration_minutes": 0,
                    "utility": 0.0,
                    "indoor": True,
                }
            )
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    raise GeoFactUnavailableError("; ".join(errors) or "Amap MCP returned no hotel anchor")


def fetch_weather_constraint_facts(city: str) -> list[WeatherConstraint]:
    """Fetch weather-derived constraints from Amap MCP."""
    _require_amap_mode()
    try:
        payload = call_tool(settings.amap_mcp_weather_tool, {"city": normalize_amap_city(city)})
        return [WeatherConstraint.model_validate(item) for item in payload or []]
    except (MCPToolError, ValueError, TypeError) as exc:
        raise GeoFactUnavailableError(str(exc)) from exc


def fetch_weather_forecast_facts(city: str, days: int) -> list[DailyWeatherForecast]:
    """Fetch user-facing multi-day weather facts from Amap MCP."""
    _require_amap_mode()
    errors: list[str] = []
    try:
        payload = call_tool(settings.amap_mcp_weather_tool, {"city": normalize_amap_city(city), "days": days})
        forecasts = _coerce_weather_forecasts(payload, days, source="amap:mcp")
        if forecasts:
            return forecasts
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    try:
        city_context = _official_city_context(city)
        city_value = city_context.get("adcode") or city_context.get("city") or city
        payload = call_tool(OFFICIAL_AMAP_WEATHER_TOOL, {"city": city_value})
        forecasts = _coerce_weather_forecasts(payload, days, source="amap:official-mcp")
        if forecasts:
            return forecasts
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    detail = f": {'; '.join(errors)}" if errors else ""
    raise GeoFactUnavailableError(f"Amap MCP returned no weather forecast{detail}")


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


def _search_official_amap_pois(city: str, keywords: list[str], limit: int) -> list[POICandidate]:
    """Read raw POIs from the official Amap MCP tool set."""
    city_context = _official_city_context(city)
    city_value = city_context.get("adcode") or city_context.get("city") or city
    pois: list[POICandidate] = []
    seen: set[str] = set()
    for keyword in _official_keyword_queries(keywords):
        payload = call_tool(
            OFFICIAL_AMAP_TEXT_SEARCH_TOOL,
            {
                "keywords": keyword,
                "city": city_value,
                "citylimit": True,
            },
        )
        for raw in _extract_raw_pois(payload):
            raw_id = str(raw.get("id") or "")
            detail = raw
            if raw_id and not raw.get("location"):
                try:
                    detail_payload = call_tool(OFFICIAL_AMAP_SEARCH_DETAIL_TOOL, {"id": raw_id})
                    if isinstance(detail_payload, dict):
                        detail = {**raw, **detail_payload}
                except MCPToolError:
                    detail = raw
            poi = _poi_from_official_amap(detail, fallback_category=keyword)
            if poi is None or poi.id in seen:
                continue
            seen.add(poi.id)
            pois.append(poi)
            if len(pois) >= limit:
                return pois
    return pois


def _official_city_context(city: str) -> dict[str, str]:
    try:
        payload = call_tool(OFFICIAL_AMAP_GEO_TOOL, {"address": city})
    except MCPToolError:
        return {"city": city}
    if not isinstance(payload, dict):
        return {"city": city}
    results = payload.get("results") or payload.get("geocodes") or []
    if not isinstance(results, list) or not results:
        return {"city": city}
    first = results[0]
    if not isinstance(first, dict):
        return {"city": city}
    return {
        "adcode": str(first.get("adcode") or ""),
        "city": str(first.get("city") or city),
        "location": str(first.get("location") or ""),
    }


def _official_keyword_queries(keywords: list[str]) -> list[str]:
    queries: list[str] = []
    for keyword in keywords or ["attraction"]:
        normalized = str(keyword).strip()
        aliases = OFFICIAL_KEYWORDS.get(normalized.lower(), [normalized])
        for alias in aliases:
            if alias and alias not in queries:
                queries.append(alias)
    if "attraction" not in queries:
        queries.append("attraction")
    return queries


def _extract_raw_pois(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("pois", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_poi_candidates(payload: object, fallback_category: str) -> list[POICandidate]:
    candidates: list[POICandidate] = []
    for item in _extract_raw_pois(payload):
        try:
            candidates.append(POICandidate.model_validate(item))
            continue
        except ValueError:
            pass
        poi = _poi_from_official_amap(item, fallback_category=fallback_category)
        if poi is not None:
            candidates.append(poi)
    return candidates


def _poi_from_official_amap(item: dict, fallback_category: str) -> POICandidate | None:
    location = str(item.get("location") or "")
    if "," not in location:
        return None
    lng_text, lat_text = location.split(",", 1)
    try:
        lng = float(lng_text)
        lat = float(lat_text)
    except ValueError:
        return None
    raw_id = str(item.get("id") or item.get("name") or f"{lat},{lng}")
    category = _normalize_official_category(
        str(item.get("type") or item.get("typecode") or fallback_category)
    )
    rating = _float_or_default(item.get("rating"), _estimated_utility(category))
    return POICandidate(
        id=f"amap_{raw_id}".replace(" ", "_"),
        name=str(item.get("name") or fallback_category),
        category=category,
        coordinates=Coordinates(lat=lat, lng=lng),
        fixed_cost=_estimated_ticket_cost(category),
        visit_duration_minutes=_estimated_duration(category),
        utility=min(10.0, max(1.0, rating * 2 if rating <= 5 else rating)),
        indoor=category in {"museum", "gallery", "shopping", "hotel", "library"},
    )


def _normalize_official_category(raw: str) -> str:
    text = raw.lower()
    if "1401" in text or "museum" in text or "博物" in raw:
        return "museum"
    if "1402" in text or "gallery" in text or "art" in text or "美术" in raw or "艺术" in raw:
        return "gallery"
    if "1101" in text or "park" in text or "garden" in text or "公园" in raw or "园林" in raw:
        return "garden"
    if "050" in text or "food" in text or "restaurant" in text or "餐" in raw or "美食" in raw:
        return "food"
    if "100" in text or "hotel" in text or "酒店" in raw:
        return "hotel"
    if "060" in text or "shopping" in text or "mall" in text or "购物" in raw or "商场" in raw:
        return "shopping"
    if "library" in text or "图书" in raw or "书店" in raw:
        return "library"
    return "landmark"


def _estimated_ticket_cost(category: str) -> float:
    return {
        "museum": 40.0,
        "gallery": 55.0,
        "garden": 30.0,
        "food": 20.0,
        "shopping": 0.0,
        "hotel": 0.0,
        "library": 0.0,
    }.get(category, 50.0)


def _estimated_duration(category: str) -> int:
    return {
        "museum": 120,
        "gallery": 100,
        "garden": 90,
        "food": 75,
        "shopping": 90,
        "hotel": 0,
        "library": 60,
    }.get(category, 90)


def _estimated_utility(category: str) -> float:
    return {
        "museum": 8.8,
        "gallery": 8.0,
        "garden": 8.2,
        "food": 7.4,
        "shopping": 6.8,
        "hotel": 0.0,
        "library": 6.4,
    }.get(category, 7.6)


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_weather_forecasts(payload: object, days: int, source: str) -> list[DailyWeatherForecast]:
    if isinstance(payload, list):
        forecasts = []
        for index, item in enumerate(payload[:days], start=1):
            if isinstance(item, DailyWeatherForecast):
                forecasts.append(item)
                continue
            if isinstance(item, dict):
                forecast = _forecast_from_dict(item, day=index, source=source)
                if forecast is not None:
                    forecasts.append(forecast)
        return forecasts

    if not isinstance(payload, dict):
        return []

    forecasts: list[DailyWeatherForecast] = []
    for forecast_group in payload.get("forecasts") or []:
        if not isinstance(forecast_group, dict):
            continue
        casts = forecast_group.get("casts") or []
        if isinstance(casts, list):
            for index, item in enumerate(casts[:days], start=1):
                if isinstance(item, dict):
                    forecast = _forecast_from_amap_cast(item, day=index, source=source)
                    if forecast is not None:
                        forecasts.append(forecast)
    if forecasts:
        return forecasts[:days]

    lives = payload.get("lives") or []
    if isinstance(lives, list) and lives:
        live = lives[0]
        if isinstance(live, dict):
            forecast = _forecast_from_live_weather(live, source=source)
            return [forecast] if forecast is not None else []

    return []


def _forecast_from_amap_cast(item: dict, day: int, source: str) -> DailyWeatherForecast | None:
    weather = str(item.get("dayweather") or item.get("nightweather") or item.get("weather") or "")
    temp_min = _optional_float(item.get("nighttemp") or item.get("temperature_min"))
    temp_max = _optional_float(item.get("daytemp") or item.get("temperature_max"))
    return DailyWeatherForecast(
        day=day,
        date=str(item.get("date") or ""),
        weather=weather,
        temperature_min=temp_min,
        temperature_max=temp_max,
        wind=str(item.get("daywind") or item.get("nightwind") or item.get("wind") or ""),
        advisory=_weather_advisory(weather, temp_min, temp_max),
        source=source,
    )


def _forecast_from_live_weather(item: dict, source: str) -> DailyWeatherForecast | None:
    weather = str(item.get("weather") or "")
    temperature = _optional_float(item.get("temperature"))
    return DailyWeatherForecast(
        day=1,
        weather=weather,
        temperature_min=temperature,
        temperature_max=temperature,
        wind=str(item.get("winddirection") or item.get("windpower") or ""),
        advisory=_weather_advisory(weather, temperature, temperature),
        source=source,
    )


def _forecast_from_dict(item: dict, day: int, source: str) -> DailyWeatherForecast | None:
    if "time_window" in item and "rule" in item and not {"weather", "dayweather", "temperature"}.intersection(item):
        return None
    try:
        return DailyWeatherForecast.model_validate({**item, "day": item.get("day") or day, "source": item.get("source") or source})
    except ValueError:
        return _forecast_from_amap_cast(item, day=day, source=source)


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _weather_advisory(weather: str, temp_min: float | None, temp_max: float | None) -> str:
    lowered = weather.lower()
    if any(token in weather for token in ("雨", "雪", "雷")) or any(
        token in lowered for token in ("rain", "snow", "storm")
    ):
        return "Prefer indoor stops and carry rain gear."
    if temp_max is not None and temp_max >= 34:
        return "Avoid long outdoor visits in the afternoon heat."
    if temp_min is not None and temp_min <= 5:
        return "Dress warmly and keep outdoor stays short."
    return "Suitable for regular sightseeing."
