from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.intent_agent import CITY_ALIASES, PREFERENCE_KEYWORDS
from app.core.config import settings
from app.graph.state import Coordinates, DailyWeatherForecast, FinancialContext, MatrixEdge, POICandidate, WeatherConstraint
from app.services.mcp_client import MCPToolError, call_tool


class GeoFactUnavailableError(RuntimeError):
    """Raised when the Amap MCP fact layer cannot provide usable data."""


OFFICIAL_AMAP_TEXT_SEARCH_TOOL = "maps_text_search"
OFFICIAL_AMAP_SEARCH_DETAIL_TOOL = "maps_search_detail"
OFFICIAL_AMAP_GEO_TOOL = "maps_geo"
OFFICIAL_AMAP_WEATHER_TOOL = "maps_weather"
OFFICIAL_AMAP_DIRECTION_DRIVING_TOOL = "maps_direction_driving"
OFFICIAL_AMAP_DIRECTION_TRANSIT_TOOL = "maps_direction_transit"
OFFICIAL_AMAP_DIRECTION_WALKING_TOOL = "maps_direction_walking"


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _ordered_keyword_aliases(preference: str, aliases: set[str]) -> list[str]:
    """Return deterministic Amap search terms derived from intent keywords."""
    ordered: list[str] = []
    for item in (preference, preference.replace("_", " ")):
        if item and item not in ordered:
            ordered.append(item)
    for alias in sorted(
        aliases,
        key=lambda value: (
            not _contains_cjk(value),
            len(value),
            value.lower(),
        ),
    ):
        if alias and alias not in ordered:
            ordered.append(alias)
    return ordered


def _build_amap_city_aliases() -> dict[str, str]:
    """Sync all intent-agent city aliases into Amap-friendly Chinese city names."""
    chinese_by_destination: dict[str, str] = {}
    for alias, destination in CITY_ALIASES.items():
        if _contains_cjk(alias):
            chinese_by_destination.setdefault(destination.lower(), alias)

    aliases: dict[str, str] = {}
    for alias, destination in CITY_ALIASES.items():
        amap_city = chinese_by_destination.get(destination.lower(), destination)
        aliases[alias.lower()] = amap_city
        aliases[destination.lower()] = amap_city
    return aliases


def _build_official_keywords() -> dict[str, list[str]]:
    """Sync intent-agent preference keywords into Amap text-search terms."""
    return {
        preference: _ordered_keyword_aliases(preference, aliases)
        for preference, aliases in PREFERENCE_KEYWORDS.items()
    }


AMAP_CITY_ALIASES = _build_amap_city_aliases()
OFFICIAL_KEYWORDS = _build_official_keywords()


@dataclass(frozen=True)
class DirectionFact:
    """Normalized travel fact from Amap direction tools."""

    distance_km: float
    duration_minutes: int
    cost: float | None = None
    boarding_station: str = ""
    alighting_station: str = ""
    note: str = ""
    polyline: list[Coordinates] = field(default_factory=list)


def amap_mcp_enabled() -> bool:
    """Return true when Amap should be used as the geographic fact source."""
    return settings.provider_mode.lower() == "amap"


def search_poi_facts(city: str, keywords: list[str], limit: int = 10) -> list[POICandidate]:
    """Fetch POI facts from Amap MCP without making route decisions."""
    _require_amap_mode()
    errors: list[str] = []
    try:
        pois = _search_configured_amap_pois_balanced(city, keywords or ["attraction"], limit)
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
    city: str = "",
) -> dict[str, MatrixEdge]:
    """Fetch the road-network tensor from Amap MCP."""
    _require_amap_mode()
    errors: list[str] = []
    try:
        payload = call_tool(
            settings.amap_mcp_matrix_tool,
            {
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "financial": financial.model_dump(mode="json"),
                "city": normalize_amap_city(city) if city else "",
            },
        )
        matrix = {
            key: MatrixEdge.model_validate(value)
            for key, value in (payload or {}).items()
        }
        if matrix:
            return matrix
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    try:
        matrix = _build_official_direction_matrix(nodes, financial, city)
        if matrix:
            return matrix
    except (MCPToolError, ValueError, TypeError) as exc:
        errors.append(str(exc))

    detail = f": {'; '.join(errors)}" if errors else ""
    raise GeoFactUnavailableError(f"Amap MCP returned an empty matrix{detail}")


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


def _search_configured_amap_pois_balanced(city: str, keywords: list[str], limit: int) -> list[POICandidate]:
    """Call the configured POI tool per preference so early keywords cannot monopolize results."""
    normalized_city = normalize_amap_city(city)
    candidates: list[POICandidate] = []
    seen: set[str] = set()
    per_keyword_limit = max(1, (limit + max(len(keywords), 1) - 1) // max(len(keywords), 1))
    for keyword in _unique_keywords(keywords):
        keyword_terms = _amap_keyword_queries([keyword])
        # Send only a limited subset of keyword terms per round to avoid hitting
        # the Amap free-tier QPS cap (~5 queries/second). Stop as soon as we
        # have enough candidates.
        term_limit = max(1, min(len(keyword_terms), per_keyword_limit + 1))
        for term in keyword_terms[:term_limit]:
            payload = call_tool(
                settings.amap_mcp_poi_tool,
                {
                    "city": normalized_city,
                    "keywords": [term],
                    "limit": per_keyword_limit,
                },
            )
            for poi in _coerce_poi_candidates(payload, fallback_category=keyword)[:per_keyword_limit]:
                if poi.id in seen:
                    continue
                seen.add(poi.id)
                candidates.append(poi)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    if len(candidates) < limit:
        payload = call_tool(
            settings.amap_mcp_poi_tool,
            {
                "city": normalized_city,
                "keywords": _amap_keyword_queries(keywords or ["attraction"]),
                "limit": limit,
            },
        )
        for poi in _coerce_poi_candidates(payload, fallback_category=(keywords or ["attraction"])[0]):
            if poi.id in seen:
                continue
            seen.add(poi.id)
            candidates.append(poi)
            if len(candidates) >= limit:
                break
    return candidates[:limit]


def _search_official_amap_pois(city: str, keywords: list[str], limit: int) -> list[POICandidate]:
    """Read raw POIs from the official Amap MCP tool set."""
    city_context = _official_city_context(city)
    city_value = city_context.get("adcode") or city_context.get("city") or city
    pois: list[POICandidate] = []
    seen: set[str] = set()
    queries = _official_keyword_queries(keywords)
    per_query_limit = max(1, (limit + max(len(queries), 1) - 1) // max(len(queries), 1))
    for keyword in queries:
        payload = call_tool(
            OFFICIAL_AMAP_TEXT_SEARCH_TOOL,
            {
                "keywords": keyword,
                "city": city_value,
                "citylimit": True,
            },
        )
        for raw in _extract_raw_pois(payload)[:per_query_limit]:
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
        return pois[:limit]

    for keyword in queries:
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
                return pois[:limit]
    return pois[:limit]


def _unique_keywords(keywords: list[str]) -> list[str]:
    unique: list[str] = []
    for keyword in keywords or ["attraction"]:
        normalized = str(keyword).strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique or ["attraction"]


def _amap_keyword_queries(keywords: list[str]) -> list[str]:
    queries: list[str] = []
    for keyword in keywords or ["attraction"]:
        normalized = str(keyword).strip()
        aliases = OFFICIAL_KEYWORDS.get(normalized.lower(), [normalized])
        for alias in aliases:
            if alias and alias not in queries:
                queries.append(alias)
    return queries or ["attraction"]


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
    business = _business_extension(item)
    rating = _float_or_default(
        business.get("rating") or item.get("rating"),
        _estimated_utility(category),
    )
    fixed_cost = _cost_for_category(category, _business_cost(item))
    return POICandidate(
        id=f"amap_{raw_id}".replace(" ", "_"),
        name=str(item.get("name") or fallback_category),
        category=category,
        coordinates=Coordinates(lat=lat, lng=lng),
        fixed_cost=fixed_cost,
        visit_duration_minutes=_estimated_duration(category),
        utility=min(10.0, max(1.0, rating * 2 if rating <= 5 else rating)),
        indoor=category in {"museum", "gallery", "shopping", "hotel", "library"},
    )


def _business_extension(item: dict) -> dict:
    value = item.get("biz_ext") or item.get("bizExt") or {}
    return value if isinstance(value, dict) else {}


def _business_cost(item: dict) -> float | None:
    business = _business_extension(item)
    for value in (
        business.get("cost"),
        business.get("price"),
        item.get("cost"),
        item.get("price"),
        item.get("avg_cost"),
    ):
        parsed = _optional_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _cost_for_category(category: str, business_cost: float | None) -> float:
    if business_cost is None:
        return _estimated_ticket_cost(category)
    if category in {"museum", "gallery", "garden", "landmark", "food", "hotel"}:
        return business_cost
    return _estimated_ticket_cost(category)


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


def _build_official_direction_matrix(
    nodes: list[POICandidate],
    financial: FinancialContext,
    city: str,
) -> dict[str, MatrixEdge]:
    """Build matrix edges from official Amap MCP direction tools."""
    matrix: dict[str, MatrixEdge] = {}
    normalized_city = normalize_amap_city(city) if city else ""
    for origin in nodes:
        for destination in nodes:
            if origin.id == destination.id:
                continue
            driving = _official_direction_fact(
                OFFICIAL_AMAP_DIRECTION_DRIVING_TOOL,
                origin,
                destination,
                normalized_city,
            )
            walking = None
            if driving.distance_km <= 1.2:
                walking = _official_direction_fact(
                    OFFICIAL_AMAP_DIRECTION_WALKING_TOOL,
                    origin,
                    destination,
                    normalized_city,
                    required=False,
                )
            transit = _official_direction_fact(
                OFFICIAL_AMAP_DIRECTION_TRANSIT_TOOL,
                origin,
                destination,
                normalized_city,
                required=False,
            )
            for hour in range(24):
                mode, fact = _choose_direction_fact(driving, transit, walking, financial, hour)
                duration = fact.duration_minutes
                cost = fact.cost if fact.cost is not None else _fallback_cost(fact.distance_km, mode, financial, hour)
                if mode == "Driving":
                    duration = max(1, round(duration * _traffic_multiplier(hour)))
                    cost = round(cost * _traffic_multiplier(hour), 2)
                matrix[f"{origin.id}->{destination.id}@{hour:02d}"] = MatrixEdge(
                    origin_id=origin.id,
                    destination_id=destination.id,
                    hour=hour,
                    distance_km=fact.distance_km,
                    duration_minutes=duration,
                    mode=mode,
                    cost=round(cost, 2),
                    boarding_station=fact.boarding_station,
                    alighting_station=fact.alighting_station,
                    transit_note=fact.note,
                    polyline=fact.polyline,
                )
    return matrix


def _official_direction_fact(
    tool_name: str,
    origin: POICandidate,
    destination: POICandidate,
    city: str,
    required: bool = True,
) -> DirectionFact:
    arguments = {
        "origin": _location_text(origin),
        "destination": _location_text(destination),
    }
    if city:
        arguments["city"] = city
        arguments["cityd"] = city
    try:
        payload = call_tool(tool_name, arguments)
    except MCPToolError:
        if required:
            raise
        return DirectionFact(distance_km=0.0, duration_minutes=0)
    fact = _direction_fact_from_payload(payload, tool_name)
    if fact is None:
        if required:
            raise GeoFactUnavailableError(f"{tool_name} returned no usable route")
        return DirectionFact(distance_km=0.0, duration_minutes=0)
    return fact


def _direction_fact_from_payload(payload: object, tool_name: str) -> DirectionFact | None:
    if not isinstance(payload, dict):
        return None
    route = payload.get("route") if isinstance(payload.get("route"), dict) else payload
    paths = route.get("paths") if isinstance(route, dict) else None
    if isinstance(paths, list) and paths:
        path = next((item for item in paths if isinstance(item, dict)), None)
        if path is None:
            return None
        distance = _optional_float(path.get("distance") or route.get("distance"))
        duration = _optional_float(path.get("duration") or route.get("duration"))
        cost = _first_optional_float(
            route.get("taxi_cost"),
            path.get("taxi_cost"),
            path.get("cost"),
            path.get("tolls"),
        )
        if distance is None or duration is None:
            return None
        return DirectionFact(
            distance_km=round(distance / 1000, 2),
            duration_minutes=max(1, round(duration / 60)),
            cost=cost,
            polyline=_path_polyline(path),
        )

    transits = route.get("transits") if isinstance(route, dict) else None
    if isinstance(transits, list) and transits:
        transit = next((item for item in transits if isinstance(item, dict)), None)
        if transit is None:
            return None
        distance = _optional_float(transit.get("distance") or route.get("distance"))
        duration = _optional_float(transit.get("duration") or route.get("duration"))
        cost = _first_optional_float(transit.get("cost"), route.get("cost"))
        if distance is None or duration is None:
            return None
        boarding, alighting = _transit_stations(transit)
        return DirectionFact(
            distance_km=round(distance / 1000, 2),
            duration_minutes=max(1, round(duration / 60)),
            cost=cost,
            boarding_station=boarding,
            alighting_station=alighting,
            note=_transit_note(boarding, alighting),
            polyline=_transit_polyline(transit),
        )

    distance = _optional_float(payload.get("distance"))
    duration = _optional_float(payload.get("duration"))
    if distance is not None and duration is not None:
        return DirectionFact(
            distance_km=round(distance / 1000, 2),
            duration_minutes=max(1, round(duration / 60)),
            cost=_first_optional_float(payload.get("cost"), payload.get("taxi_cost")),
            polyline=_polyline_coordinates(payload.get("polyline")),
        )
    return None


def _choose_direction_fact(
    driving: DirectionFact,
    transit: DirectionFact,
    walking: DirectionFact | None,
    financial: FinancialContext,
    hour: int,
) -> tuple[str, DirectionFact]:
    if walking is not None and walking.duration_minutes > 0 and walking.distance_km <= 1.2:
        return "Walking", walking
    driving_cost = driving.cost if driving.cost is not None else _fallback_cost(driving.distance_km, "Driving", financial, hour)
    if transit.duration_minutes > 0:
        transit_cost = transit.cost if transit.cost is not None else financial.base_transit_fare
        if driving_cost > transit_cost * 2.2 and transit.duration_minutes - driving.duration_minutes <= 20:
            return "Transit", transit
    return "Driving", driving


def _location_text(node: POICandidate) -> str:
    return f"{node.coordinates.lng},{node.coordinates.lat}"


def _traffic_multiplier(hour: int) -> float:
    if 7 <= hour <= 9:
        return 1.35
    if 17 <= hour <= 19:
        return 1.45
    if 22 <= hour or hour <= 5:
        return 0.85
    return 1.0


def _fallback_cost(distance_km: float, mode: str, financial: FinancialContext, hour: int) -> float:
    if mode == "Walking":
        return 0.0
    if mode == "Transit":
        return financial.base_transit_fare
    return distance_km * financial.driving_rate_per_km * _traffic_multiplier(hour)


def _first_optional_float(*values: object) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _transit_stations(transit: dict) -> tuple[str, str]:
    segments = transit.get("segments") or []
    if not isinstance(segments, list):
        return "", ""
    boarding = ""
    alighting = ""
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        buslines = bus.get("buslines") if isinstance(bus, dict) else []
        if not isinstance(buslines, list) or not buslines:
            continue
        line = next((item for item in buslines if isinstance(item, dict)), None)
        if line is None:
            continue
        departure = line.get("departure_stop") if isinstance(line.get("departure_stop"), dict) else {}
        arrival = line.get("arrival_stop") if isinstance(line.get("arrival_stop"), dict) else {}
        boarding = boarding or str(departure.get("name") or "")
        alighting = str(arrival.get("name") or alighting)
    return boarding, alighting


def _path_polyline(path: dict) -> list[Coordinates]:
    points = _polyline_coordinates(path.get("polyline"))
    steps = path.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                points.extend(_polyline_coordinates(step.get("polyline")))
    return _dedupe_coordinates(points)


def _transit_polyline(transit: dict) -> list[Coordinates]:
    points: list[Coordinates] = []
    segments = transit.get("segments") or []
    if not isinstance(segments, list):
        return points
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        walking = segment.get("walking") if isinstance(segment.get("walking"), dict) else {}
        walking_steps = walking.get("steps") if isinstance(walking, dict) else []
        if isinstance(walking_steps, list):
            for step in walking_steps:
                if isinstance(step, dict):
                    points.extend(_polyline_coordinates(step.get("polyline")))
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        buslines = bus.get("buslines") if isinstance(bus, dict) else []
        if isinstance(buslines, list):
            for line in buslines:
                if isinstance(line, dict):
                    points.extend(_polyline_coordinates(line.get("polyline")))
    return _dedupe_coordinates(points)


def _polyline_coordinates(value: object) -> list[Coordinates]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        points: list[Coordinates] = []
        for item in value:
            if isinstance(item, Coordinates):
                points.append(item)
            elif isinstance(item, dict):
                lat = _optional_float(item.get("lat") or item.get("latitude"))
                lng = _optional_float(item.get("lng") or item.get("lon") or item.get("longitude"))
                if lat is not None and lng is not None:
                    points.append(Coordinates(lat=lat, lng=lng))
            elif isinstance(item, str):
                points.extend(_polyline_coordinates(item))
        return _dedupe_coordinates(points)

    points = []
    for raw_pair in str(value).split(";"):
        pair = raw_pair.strip()
        if not pair or "," not in pair:
            continue
        lng_text, lat_text = pair.split(",", 1)
        lat = _optional_float(lat_text)
        lng = _optional_float(lng_text)
        if lat is not None and lng is not None:
            points.append(Coordinates(lat=lat, lng=lng))
    return _dedupe_coordinates(points)


def _dedupe_coordinates(points: list[Coordinates]) -> list[Coordinates]:
    deduped: list[Coordinates] = []
    for point in points:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    return deduped


def _transit_note(boarding: str, alighting: str) -> str:
    if boarding and alighting:
        return f"Board at {boarding}; alight at {alighting}."
    if boarding:
        return f"Board at {boarding}."
    return ""


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
