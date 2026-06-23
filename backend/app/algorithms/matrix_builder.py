from __future__ import annotations

from app.algorithms.geo import haversine_km, minutes_from_distance
from app.core.config import settings
from app.graph.state import FinancialContext, MatrixEdge, POICandidate, TransportMode

HOUR_RANGE = range(24)


def matrix_key(origin_id: str, destination_id: str, hour: int = 9) -> str:
    """Stable key for a directed edge at a discrete hour."""
    return f"{origin_id}->{destination_id}@{hour:02d}"


def traffic_multiplier(hour: int) -> float:
    """Return a simple time-dependent city congestion multiplier."""
    if 7 <= hour <= 9:
        return 1.35
    if 17 <= hour <= 19:
        return 1.45
    if 22 <= hour or hour <= 5:
        return 0.85
    return 1.0


def choose_mode(
    distance_km: float,
    driving_cost: float,
    transit_cost: float,
    hour: int = 9,
) -> TransportMode:
    """Apply the PRD's mode downgrade rule to protect the budget line."""
    if distance_km <= 1.2:
        return TransportMode.walking
    driving_minutes = round(minutes_from_distance(distance_km, "Driving") * traffic_multiplier(hour))
    driving_saves_minutes = minutes_from_distance(distance_km, "Transit") - driving_minutes
    if driving_cost > transit_cost * 2.2 and driving_saves_minutes <= 15:
        return TransportMode.transit
    return TransportMode.driving


def _edge_cost(distance_km: float, mode: TransportMode, financial: FinancialContext, hour: int) -> float:
    if mode == TransportMode.walking:
        return 0.0
    if mode == TransportMode.transit:
        return financial.base_transit_fare
    return distance_km * financial.driving_rate_per_km * traffic_multiplier(hour)


def _edge_duration(distance_km: float, mode: TransportMode, hour: int) -> int:
    duration = minutes_from_distance(distance_km, mode.value)
    if mode == TransportMode.driving:
        duration = round(duration * traffic_multiplier(hour))
    return max(1, duration)


def build_fallback_matrix(
    nodes: list[POICandidate],
    financial: FinancialContext,
    detour_factor: float | None = None,
) -> dict[str, MatrixEdge]:
    factor = detour_factor or settings.city_detour_factor
    matrix: dict[str, MatrixEdge] = {}

    # This fallback mirrors the SLA section: if an external road matrix is not
    # available, approximate road distance with Haversine * city detour factor.
    for origin in nodes:
        for destination in nodes:
            if origin.id == destination.id:
                continue
            physical_km = haversine_km(origin.coordinates, destination.coordinates)
            road_km = round(physical_km * factor, 2)
            for hour in HOUR_RANGE:
                driving_cost = road_km * financial.driving_rate_per_km * traffic_multiplier(hour)
                mode = choose_mode(road_km, driving_cost, financial.base_transit_fare, hour)
                matrix[matrix_key(origin.id, destination.id, hour)] = MatrixEdge(
                    origin_id=origin.id,
                    destination_id=destination.id,
                    hour=hour,
                    distance_km=road_km,
                    duration_minutes=_edge_duration(road_km, mode, hour),
                    mode=mode,
                    cost=round(_edge_cost(road_km, mode, financial, hour), 2),
                )
    return matrix
