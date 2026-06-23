from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from app.graph.state import Coordinates


def haversine_km(origin: Coordinates, destination: Coordinates) -> float:
    """Return spherical distance in kilometers between two coordinates."""
    radius_km = 6371.0
    lat1, lng1 = radians(origin.lat), radians(origin.lng)
    lat2, lng2 = radians(destination.lat), radians(destination.lng)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * radius_km * asin(sqrt(a))


def minutes_from_distance(distance_km: float, mode: str) -> int:
    """Estimate travel time from distance when no live road API is available."""
    speed_by_mode = {
        "Walking": 4.5,
        "Transit": 22.0,
        "Driving": 28.0,
    }
    speed = speed_by_mode.get(mode, 22.0)
    return max(1, round((distance_km / speed) * 60))
