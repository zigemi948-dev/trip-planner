from __future__ import annotations

from math import ceil, hypot

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


def build_route_geometry(hotel: POICandidate, route: DayRoute, return_geometry: list[Coordinates] | None = None) -> list[Coordinates]:
    """Build route geometry from provider polylines, with map-safe fallback segments.

    The optional ``return_geometry`` parameter lets callers supply the polyline
    for the final leg (last stop -> hotel). When provided, the road-following
    path is preserved instead of rendering a straight line."""
    if not route.stops:
        return [hotel.coordinates]

    geometry: list[Coordinates] = []
    previous = hotel.coordinates
    for stop in route.stops:
        geometry = _append_route_segment(geometry, previous, stop.poi.coordinates, stop.inbound_geometry)
        previous = stop.poi.coordinates

    # Return leg: use the caller-supplied return_geometry when available
    # so the road-following path is preserved instead of a straight line.
    # Note: we DO NOT reverse the last inbound polyline—that polyline is the
    #       path FROM the previous stop (or hotel) TO the last stop,
    #       which is the opposite direction of what the return leg needs.
    geometry = _append_route_segment(geometry, previous, hotel.coordinates, return_geometry)
    return geometry



def _append_route_segment(
    geometry: list[Coordinates],
    origin: Coordinates,
    destination: Coordinates,
    provider_polyline: list[Coordinates],
) -> list[Coordinates]:
    if not provider_polyline:
        return _append_segment(geometry, origin, destination)

    next_geometry = geometry
    segment = provider_polyline
    if not next_geometry and origin == segment[0]:
        next_geometry = [origin]
    elif not next_geometry:
        next_geometry = _append_segment(next_geometry, origin, segment[0])
    elif next_geometry[-1] != segment[0]:
        next_geometry = _append_segment(next_geometry, next_geometry[-1], segment[0])
    if next_geometry and segment and next_geometry[-1] == segment[0]:
        segment = segment[1:]
    next_geometry = [*next_geometry, *segment]
    if next_geometry[-1] != destination:
        next_geometry = _append_segment(next_geometry, next_geometry[-1], destination)
    return next_geometry


def _append_segment(
    geometry: list[Coordinates],
    origin: Coordinates,
    destination: Coordinates,
) -> list[Coordinates]:
    steps = _segment_steps(origin, destination)
    segment = interpolate_segment(origin, destination, steps=steps)
    if geometry:
        segment = segment[1:]
    return [*geometry, *segment]


def _segment_steps(origin: Coordinates, destination: Coordinates) -> int:
    coordinate_distance = hypot(destination.lat - origin.lat, destination.lng - origin.lng)
    return max(3, min(18, ceil(coordinate_distance / 0.003)))


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


def attach_route_geometry(hotel: POICandidate, route: DayRoute, return_geometry: list[Coordinates] | None = None) -> DayRoute:
    """Attach simplified geometry and bounds to a day route.

    The optional ``return_geometry`` is forwarded to ``build_route_geometry``.
    """
    dense = build_route_geometry(hotel, route, return_geometry=return_geometry)
    if any(stop.inbound_geometry for stop in route.stops):
        simplified = dense
    else:
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
