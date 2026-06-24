from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.algorithms.matrix_builder import build_fallback_matrix, matrix_key, traffic_multiplier
from app.core.config import settings
from app.graph.state import Coordinates, FinancialContext, MatrixEdge, POICandidate, TransportMode, WeatherConstraint


class AmapUnavailableError(RuntimeError):
    """Raised when Amap cannot return usable provider data."""


def amap_is_enabled() -> bool:
    return settings.provider_mode.lower() == "amap" and bool(settings.amap_api_key)


def search_pois(
    city: str,
    keywords: list[str],
    category: str = "attraction",
    limit: int = 10,
) -> list[POICandidate]:
    """Search Amap text POIs and map them into solver-safe candidates."""
    if not amap_is_enabled():
        raise AmapUnavailableError("Amap provider is not enabled")

    seen: set[str] = set()
    pois: list[POICandidate] = []
    for keyword in keywords or [category]:
        payload = _request_json(
            "/place/text",
            {
                "keywords": keyword,
                "city": city,
                "offset": limit,
                "page": 1,
                "extensions": "base",
            },
        )
        for item in payload.get("pois", []):
            poi = _poi_from_amap(item, fallback_category=keyword)
            if poi is None or poi.id in seen:
                continue
            seen.add(poi.id)
            pois.append(poi)
            if len(pois) >= limit:
                return pois
    if not pois:
        raise AmapUnavailableError("Amap returned no POIs")
    return pois


def resolve_hotel(city: str) -> POICandidate:
    """Resolve a central hotel anchor from Amap text search."""
    hotels = search_pois(city, ["酒店", "hotel"], category="hotel", limit=1)
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


def fetch_weather_constraints(city: str) -> list[WeatherConstraint]:
    """Fetch Amap live weather and convert it into solver constraints."""
    if not amap_is_enabled():
        raise AmapUnavailableError("Amap provider is not enabled")

    payload = _request_json(
        "/weather/weatherInfo",
        {
            "city": city,
            "extensions": "base",
        },
    )
    lives = payload.get("lives") or []
    if not lives:
        raise AmapUnavailableError("Amap returned no weather lives")
    live = lives[0]
    weather_text = str(live.get("weather") or "")
    temperature_text = str(live.get("temperature") or "")
    constraints: list[WeatherConstraint] = []

    if any(token in weather_text for token in ("雨", "雪", "雷", "storm", "rain", "snow")):
        constraints.append(
            WeatherConstraint(
                time_window=("09:00", "18:00"),
                rule="avoid_outdoor",
                block_outdoor=True,
                reason=f"amap_weather:{weather_text}",
            )
        )

    try:
        temperature = float(temperature_text)
    except ValueError:
        temperature = 0
    if temperature >= 34:
        constraints.append(
            WeatherConstraint(
                time_window=("13:00", "16:00"),
                rule="avoid_outdoor",
                block_outdoor=True,
                reason=f"amap_heat:{temperature_text}",
            )
        )

    return constraints


def build_amap_matrix(nodes: list[POICandidate], financial: FinancialContext) -> dict[str, MatrixEdge]:
    """Build directed travel edges from Amap distance data with fallback shape."""
    if not amap_is_enabled():
        raise AmapUnavailableError("Amap provider is not enabled")

    fallback = build_fallback_matrix(nodes, financial)
    matrix: dict[str, MatrixEdge] = {}
    for origin in nodes:
        destinations = [node for node in nodes if node.id != origin.id]
        if not destinations:
            continue
        try:
            distances = _distance_rows(origin, destinations)
        except AmapUnavailableError:
            distances = {}
        for destination in destinations:
            fallback_edge = fallback[matrix_key(origin.id, destination.id, 9)]
            meters, seconds = distances.get(destination.id, (fallback_edge.distance_km * 1000, fallback_edge.duration_minutes * 60))
            distance_km = round(float(meters) / 1000, 2)
            base_minutes = max(1, round(float(seconds) / 60))
            for hour in range(24):
                mode = _mode_for_amap_edge(distance_km, base_minutes, financial, hour)
                cost = _cost_for_mode(distance_km, mode, financial, hour)
                duration = base_minutes
                if mode == TransportMode.driving:
                    duration = max(1, round(base_minutes * traffic_multiplier(hour)))
                elif mode == TransportMode.transit:
                    duration = max(1, round(base_minutes * 1.18))
                boarding_station = ""
                alighting_station = ""
                transit_note = ""
                if mode == TransportMode.transit:
                    boarding_station = f"{origin.name} nearby transit stop"
                    alighting_station = f"{destination.name} nearby transit stop"
                    transit_note = f"Board at {boarding_station}; alight at {alighting_station}."
                matrix[matrix_key(origin.id, destination.id, hour)] = MatrixEdge(
                    origin_id=origin.id,
                    destination_id=destination.id,
                    hour=hour,
                    distance_km=distance_km,
                    duration_minutes=duration,
                    mode=mode,
                    cost=round(cost, 2),
                    boarding_station=boarding_station,
                    alighting_station=alighting_station,
                    transit_note=transit_note,
                )

    if not matrix:
        raise AmapUnavailableError("Amap matrix returned no edges")
    return matrix


def _request_json(path: str, params: dict[str, object]) -> dict:
    query = urlencode({**params, "key": settings.amap_api_key})
    url = f"{settings.amap_base_url.rstrip('/')}{path}?{query}"
    try:
        with urlopen(url, timeout=settings.amap_timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AmapUnavailableError(str(exc)) from exc
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AmapUnavailableError("Amap response was not valid JSON") from exc
    if str(data.get("status")) != "1":
        raise AmapUnavailableError(str(data.get("info") or "Amap request failed"))
    return data


def _poi_from_amap(item: dict, fallback_category: str) -> POICandidate | None:
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
    category = _normalize_category(str(item.get("type") or fallback_category))
    return POICandidate(
        id=f"amap_{raw_id}".replace(" ", "_"),
        name=str(item.get("name") or fallback_category),
        category=category,
        coordinates=Coordinates(lat=lat, lng=lng),
        fixed_cost=_estimated_ticket_cost(category),
        visit_duration_minutes=_estimated_duration(category),
        utility=_estimated_utility(category),
        indoor=category in {"museum", "gallery", "shopping", "hotel"},
    )


def _distance_rows(origin: POICandidate, destinations: list[POICandidate]) -> dict[str, tuple[float, float]]:
    payload = _request_json(
        "/distance",
        {
            "origins": f"{origin.coordinates.lng},{origin.coordinates.lat}",
            "destination": "|".join(f"{node.coordinates.lng},{node.coordinates.lat}" for node in destinations),
            "type": 1,
        },
    )
    rows: dict[str, tuple[float, float]] = {}
    for index, result in enumerate(payload.get("results", [])):
        if index >= len(destinations):
            break
        rows[destinations[index].id] = (float(result.get("distance") or 0), float(result.get("duration") or 0))
    return rows


def _normalize_category(raw: str) -> str:
    text = raw.lower()
    if "博物" in raw or "museum" in text:
        return "museum"
    if "美术" in raw or "艺术" in raw or "gallery" in text or "art" in text:
        return "gallery"
    if "园" in raw or "park" in text or "garden" in text:
        return "garden"
    if "餐" in raw or "美食" in raw or "food" in text or "restaurant" in text:
        return "food"
    if "酒店" in raw or "hotel" in text:
        return "hotel"
    if "购物" in raw or "商场" in raw or "shopping" in text or "mall" in text:
        return "shopping"
    return "landmark"


def _estimated_ticket_cost(category: str) -> float:
    return {
        "museum": 40.0,
        "gallery": 55.0,
        "garden": 30.0,
        "food": 20.0,
        "shopping": 0.0,
        "hotel": 0.0,
    }.get(category, 50.0)


def _estimated_duration(category: str) -> int:
    return {
        "museum": 120,
        "gallery": 100,
        "garden": 90,
        "food": 75,
        "shopping": 90,
        "hotel": 0,
    }.get(category, 90)


def _estimated_utility(category: str) -> float:
    return {
        "museum": 8.8,
        "gallery": 8.0,
        "garden": 8.2,
        "food": 7.4,
        "shopping": 6.8,
        "hotel": 0.0,
    }.get(category, 7.6)


def _mode_for_amap_edge(
    distance_km: float,
    driving_minutes: int,
    financial: FinancialContext,
    hour: int,
) -> TransportMode:
    if distance_km <= 1.2:
        return TransportMode.walking
    driving_cost = distance_km * financial.driving_rate_per_km * traffic_multiplier(hour)
    transit_minutes = max(1, round(driving_minutes * 1.18))
    if driving_cost > financial.base_transit_fare * 2.2 and transit_minutes - driving_minutes <= 15:
        return TransportMode.transit
    return TransportMode.driving


def _cost_for_mode(distance_km: float, mode: TransportMode, financial: FinancialContext, hour: int) -> float:
    if mode == TransportMode.walking:
        return 0.0
    if mode == TransportMode.transit:
        return financial.base_transit_fare
    return distance_km * financial.driving_rate_per_km * traffic_multiplier(hour)
