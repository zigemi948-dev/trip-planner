from __future__ import annotations

from datetime import datetime, timedelta

from app.algorithms.clustering import cluster_by_day
from app.algorithms.geometry import attach_route_geometry
from app.algorithms.matrix_builder import matrix_key
from app.graph.state import (
    DayRoute,
    MatrixEdge,
    POICandidate,
    RouteStop,
    TransportMode,
    WeatherConstraint,
)


TIME_FORMAT = "%H:%M"


def add_minutes(time_text: str, minutes: int) -> str:
    """Add minutes to an HH:MM timestamp."""
    value = datetime.strptime(time_text, TIME_FORMAT)
    return (value + timedelta(minutes=minutes)).strftime(TIME_FORMAT)


def minutes_since_midnight(time_text: str) -> int:
    """Convert HH:MM text into an integer minute for comparisons."""
    value = datetime.strptime(time_text, TIME_FORMAT)
    return value.hour * 60 + value.minute


def hour_from_clock(time_text: str) -> int:
    """Return the discrete matrix hour for a concrete clock time."""
    return datetime.strptime(time_text, TIME_FORMAT).hour


def _within_window(start: str, end: str, window: tuple[str, str]) -> bool:
    return minutes_since_midnight(start) >= minutes_since_midnight(window[0]) and (
        minutes_since_midnight(end) <= minutes_since_midnight(window[1])
    )


def _overlaps_window(start: str, end: str, window: tuple[str, str]) -> bool:
    return minutes_since_midnight(start) < minutes_since_midnight(window[1]) and (
        minutes_since_midnight(end) > minutes_since_midnight(window[0])
    )


def _edge(
    matrix: dict[str, MatrixEdge],
    origin_id: str,
    destination_id: str,
    clock: str = "09:00",
) -> MatrixEdge | None:
    """Fetch a directed matrix edge for the clock's discrete time slice."""
    return matrix.get(matrix_key(origin_id, destination_id, hour_from_clock(clock))) or matrix.get(
        matrix_key(origin_id, destination_id)
    )


def _violates_weather(poi: POICandidate, arrival: str, departure: str, constraints: list[WeatherConstraint]) -> bool:
    """Check whether a POI visit collides with weather-derived exclusions."""
    for constraint in constraints:
        if not _overlaps_window(arrival, departure, constraint.time_window):
            continue
        if constraint.block_outdoor and not poi.indoor:
            return True
        if poi.category in constraint.blocked_categories:
            return True
    return False


def is_feasible_visit(
    poi: POICandidate,
    arrival: str,
    departure: str,
    constraints: list[WeatherConstraint],
) -> bool:
    """Validate opening hours and weather constraints for a concrete visit."""
    return _within_window(arrival, departure, poi.opening_window) and not _violates_weather(
        poi,
        arrival,
        departure,
        constraints,
    )


def _next_best(
    current: POICandidate,
    candidates: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    clock: str,
    constraints: list[WeatherConstraint],
) -> POICandidate:
    """Choose the nearest remaining POI, using utility as a tie-breaker."""
    return min(
        candidates,
        key=lambda poi: _candidate_score(current, poi, matrix, clock, constraints),
    )


def _candidate_score(
    current: POICandidate,
    poi: POICandidate,
    matrix: dict[str, MatrixEdge],
    clock: str,
    constraints: list[WeatherConstraint],
) -> tuple[int, float, float]:
    edge = _edge(matrix, current.id, poi.id, clock)
    travel_minutes = edge.duration_minutes if edge else 9999
    arrival = add_minutes(clock, travel_minutes)
    departure = add_minutes(arrival, poi.visit_duration_minutes)
    infeasible_penalty = 10000 if not is_feasible_visit(poi, arrival, departure, constraints) else 0
    cost = edge.cost if edge else 9999
    return (travel_minutes + infeasible_penalty, cost, -poi.utility)


def solve_routes(
    hotel: POICandidate,
    pois: list[POICandidate],
    days: int,
    matrix: dict[str, MatrixEdge],
    day_start: str = "09:00",
    weather_constraints: list[WeatherConstraint] | None = None,
) -> list[DayRoute]:
    """Solve a deterministic nearest-neighbor route for each clustered day.

    This is intentionally simple and replaceable: the surrounding workflow and
    state schema are stable, while the internals can later become NSGA-II.
    """
    clusters = cluster_by_day(pois, days)
    routes: list[DayRoute] = []
    constraints = weather_constraints or []

    for day_index, cluster in enumerate(clusters, start=1):
        remaining = list(cluster)
        current = hotel
        clock = day_start
        total_minutes = 0
        total_cost = 0.0
        stops: list[RouteStop] = []

        while remaining:
            # Walk the route greedily from the hotel/previous stop through the
            # closest candidate in the time-dependent matrix.
            poi = _next_best(current, remaining, matrix, clock, constraints)
            inbound = _edge(matrix, current.id, poi.id, clock)
            travel_minutes = inbound.duration_minutes if inbound else 0
            arrival = add_minutes(clock, travel_minutes)
            departure = add_minutes(arrival, poi.visit_duration_minutes)
            feasible = is_feasible_visit(poi, arrival, departure, constraints)

            stops.append(
                RouteStop(
                    poi=poi,
                    day=day_index,
                    arrival_time=arrival,
                    departure_time=departure,
                    inbound_mode=inbound.mode if inbound else TransportMode.walking,
                    inbound_cost=inbound.cost if inbound else 0,
                    inbound_distance_km=inbound.distance_km if inbound else 0,
                )
            )
            total_minutes += travel_minutes + poi.visit_duration_minutes
            total_cost += poi.fixed_cost + (inbound.cost if inbound else 0)
            clock = departure
            current = poi
            remaining.remove(poi)
            if not feasible:
                total_cost += 0

        infeasible_count = sum(
            1
            for stop in stops
            if not is_feasible_visit(
                stop.poi,
                stop.arrival_time,
                stop.departure_time,
                constraints,
            )
        )
        fitness = sum(stop.poi.utility for stop in stops) - total_minutes / 600 - total_cost / 500 - infeasible_count * 5
        route = DayRoute(
            day=day_index,
            stops=stops,
            total_minutes=total_minutes,
            total_cost=round(total_cost, 2),
            fitness_score=round(fitness, 3),
        )
        routes.append(attach_route_geometry(hotel, route))

    return routes


def cheapest_insertion(
    route: DayRoute,
    new_poi: POICandidate,
    hotel: POICandidate,
    matrix: dict[str, MatrixEdge],
    day_start: str = "09:00",
    weather_constraints: list[WeatherConstraint] | None = None,
) -> DayRoute:
    """Insert one POI at the position with the smallest transport cost delta."""
    candidates = route.stops[:]
    best_index = 0
    best_delta = float("inf")

    for index in range(len(candidates) + 1):
        before = hotel if index == 0 else candidates[index - 1].poi
        after = candidates[index].poi if index < len(candidates) else None
        clock = route.stops[index - 1].departure_time if index > 0 else "09:00"
        added = _edge(matrix, before.id, new_poi.id, clock)
        removed_cost = 0.0
        added_cost = added.cost if added else 0.0
        if after:
            old = _edge(matrix, before.id, after.id, clock)
            new_tail_clock = add_minutes(clock, (added.duration_minutes if added else 0) + new_poi.visit_duration_minutes)
            new_tail = _edge(matrix, new_poi.id, after.id, new_tail_clock)
            removed_cost = old.cost if old else 0.0
            added_cost += new_tail.cost if new_tail else 0.0
        delta = added_cost - removed_cost
        if delta < best_delta:
            best_delta = delta
            best_index = index

    pois = [stop.poi for stop in candidates]
    pois.insert(best_index, new_poi)
    return solve_routes(
        hotel,
        pois,
        1,
        matrix,
        day_start,
        weather_constraints=weather_constraints,
    )[0]
