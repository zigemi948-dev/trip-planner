from __future__ import annotations

from math import hypot

from app.graph.state import BoundingBox, Coordinates, DayRoute, POICandidate


def interpolate_segment(origin: Coordinates, destination: Coordinates, steps: int = 6) -> list[Coordinates]:
    """Create a dense straight-line segment between two coordinates."""
    if steps <= 1:
        return [origin, destination]

    points: list[Coordinates] = []
    for index in range(steps):
        ratio = index / (steps - 1)
        points.append(
            Coordinates(
                lat=origin.lat + (destination.lat - origin.lat) * ratio,
                lng=origin.lng + (destination.lng - origin.lng) * ratio,
            )
        )
    return points


def build_route_geometry(hotel: POICandidate, route: DayRoute) -> list[Coordinates]:
    """Build a dense route geometry from hotel to every stop."""
    if not route.stops:
        return [hotel.coordinates]

    geometry: list[Coordinates] = []
    previous = hotel.coordinates
    for stop in route.stops:
        segment = interpolate_segment(previous, stop.poi.coordinates)
        if geometry:
            segment = segment[1:]
        geometry.extend(segment)
        previous = stop.poi.coordinates
    return geometry


def simplify_geometry(points: list[Coordinates], tolerance: float = 0.0004) -> list[Coordinates]:
    """Simplify coordinates with the Douglas-Peucker algorithm."""
    if len(points) <= 2:
        return points

    first = points[0]
    last = points[-1]
    max_distance = -1.0
    max_index = 0

    for index, point in enumerate(points[1:-1], start=1):
        distance = _perpendicular_distance(point, first, last)
        if distance > max_distance:
            max_distance = distance
            max_index = index

    if max_distance > tolerance:
        left = simplify_geometry(points[: max_index + 1], tolerance)
        right = simplify_geometry(points[max_index:], tolerance)
        return left[:-1] + right

    return [first, last]


def compute_bounds(points: list[Coordinates]) -> BoundingBox | None:
    """Return the bounding box for a route geometry."""
    if not points:
        return None

    return BoundingBox(
        min_lat=min(point.lat for point in points),
        min_lng=min(point.lng for point in points),
        max_lat=max(point.lat for point in points),
        max_lng=max(point.lng for point in points),
    )


def attach_route_geometry(hotel: POICandidate, route: DayRoute) -> DayRoute:
    """Attach simplified geometry and bounds to a day route."""
    dense = build_route_geometry(hotel, route)
    simplified = simplify_geometry(dense)
    route.geometry = simplified
    route.bounds = compute_bounds(simplified)
    return route


def _perpendicular_distance(point: Coordinates, start: Coordinates, end: Coordinates) -> float:
    if start.lat == end.lat and start.lng == end.lng:
        return hypot(point.lat - start.lat, point.lng - start.lng)

    numerator = abs(
        (end.lng - start.lng) * (start.lat - point.lat)
        - (start.lng - point.lng) * (end.lat - start.lat)
    )
    denominator = hypot(end.lng - start.lng, end.lat - start.lat)
    return numerator / denominator
