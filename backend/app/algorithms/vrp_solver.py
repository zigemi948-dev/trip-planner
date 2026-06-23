from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import permutations

from app.algorithms.clustering import cluster_by_day
from app.algorithms.geometry import attach_route_geometry
from app.algorithms.matrix_builder import matrix_key
from app.core.config import settings
from app.graph.state import (
    DayRoute,
    MatrixEdge,
    POICandidate,
    RouteStop,
    TransportMode,
    WeatherConstraint,
)


TIME_FORMAT = "%H:%M"
MAX_EXACT_PERMUTATION_SIZE = 7
MAX_MUTATED_SEQUENCES = 80
TIME_PENALTY_WEIGHT = 1 / 600
COST_PENALTY_WEIGHT = 1 / 500


@dataclass(frozen=True)
class RouteCandidate:
    """One evaluated day-level TD-VRPTW candidate."""

    stops: list[RouteStop]
    total_minutes: int
    total_cost: float
    utility: float
    skipped_count: int
    fitness_score: float


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


def day_end_from_start(day_start: str, max_minutes: int | None = None) -> str:
    """Return the default daily hard horizon from the configured capacity."""
    return add_minutes(day_start, max_minutes or settings.max_day_minutes)


def _within_window(start: str, end: str, window: tuple[str, str]) -> bool:
    return minutes_since_midnight(start) >= minutes_since_midnight(window[0]) and (
        minutes_since_midnight(end) <= minutes_since_midnight(window[1])
    )


def _before(time_text: str, other: str) -> bool:
    return minutes_since_midnight(time_text) < minutes_since_midnight(other)


def _after(time_text: str, other: str) -> bool:
    return minutes_since_midnight(time_text) > minutes_since_midnight(other)


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


def _sequence_key(sequence: list[POICandidate]) -> tuple[str, ...]:
    return tuple(poi.id for poi in sequence)


def _greedy_sequence(
    hotel: POICandidate,
    pois: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    constraints: list[WeatherConstraint],
) -> list[POICandidate]:
    remaining = list(pois)
    current = hotel
    clock = day_start
    ordered: list[POICandidate] = []
    while remaining:
        poi = _next_best(current, remaining, matrix, clock, constraints)
        edge = _edge(matrix, current.id, poi.id, clock)
        clock = add_minutes(clock, (edge.duration_minutes if edge else 0) + poi.visit_duration_minutes)
        ordered.append(poi)
        remaining.remove(poi)
        current = poi
    return ordered


def _candidate_sequences(
    hotel: POICandidate,
    pois: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    constraints: list[WeatherConstraint],
) -> list[list[POICandidate]]:
    """Build a compact deterministic route population for NSGA-II-style scoring."""
    if not pois:
        return []

    base_sequences = [
        _greedy_sequence(hotel, pois, matrix, day_start, constraints),
        sorted(pois, key=lambda poi: (-poi.utility, poi.fixed_cost, poi.coordinates.lng)),
        sorted(pois, key=lambda poi: (minutes_since_midnight(poi.opening_window[0]), -poi.utility)),
        sorted(pois, key=lambda poi: (poi.coordinates.lng, poi.coordinates.lat)),
        sorted(pois, key=lambda poi: (-poi.coordinates.lng, poi.coordinates.lat)),
    ]

    sequences: list[list[POICandidate]] = []
    seen: set[tuple[str, ...]] = set()

    def add_sequence(sequence: list[POICandidate]) -> None:
        key = _sequence_key(sequence)
        if key not in seen:
            seen.add(key)
            sequences.append(sequence)

    for sequence in base_sequences:
        add_sequence(list(sequence))

    if len(pois) <= MAX_EXACT_PERMUTATION_SIZE:
        for sequence in permutations(pois):
            add_sequence(list(sequence))
    else:
        for sequence in list(sequences):
            if len(sequences) >= MAX_MUTATED_SEQUENCES:
                break
            for index in range(len(sequence) - 1):
                mutated = list(sequence)
                mutated[index], mutated[index + 1] = mutated[index + 1], mutated[index]
                add_sequence(mutated)
                if len(sequences) >= MAX_MUTATED_SEQUENCES:
                    break

    return sequences[:MAX_MUTATED_SEQUENCES]


def _evaluate_sequence(
    hotel: POICandidate,
    sequence: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    day_end: str,
    constraints: list[WeatherConstraint],
) -> RouteCandidate:
    current = hotel
    clock = day_start
    stops: list[RouteStop] = []
    total_cost = 0.0
    skipped_count = 0

    for poi in sequence:
        inbound = _edge(matrix, current.id, poi.id, clock)
        travel_minutes = inbound.duration_minutes if inbound else 0
        arrival = add_minutes(clock, travel_minutes)
        if _before(arrival, poi.opening_window[0]):
            arrival = poi.opening_window[0]
        departure = add_minutes(arrival, poi.visit_duration_minutes)

        if (
            not _within_window(arrival, departure, poi.opening_window)
            or _after(departure, day_end)
            or _violates_weather(poi, arrival, departure, constraints)
        ):
            skipped_count += 1
            continue

        stops.append(
            RouteStop(
                poi=poi,
                day=1,
                arrival_time=arrival,
                departure_time=departure,
                inbound_mode=inbound.mode if inbound else TransportMode.walking,
                inbound_cost=inbound.cost if inbound else 0,
                inbound_distance_km=inbound.distance_km if inbound else 0,
            )
        )
        total_cost += poi.fixed_cost + (inbound.cost if inbound else 0)
        current = poi
        clock = departure

    total_minutes = max(0, minutes_since_midnight(clock) - minutes_since_midnight(day_start)) if stops else 0
    utility = sum(stop.poi.utility for stop in stops)
    fitness = (
        utility
        - total_minutes * TIME_PENALTY_WEIGHT
        - total_cost * COST_PENALTY_WEIGHT
        - skipped_count * 0.75
    )
    return RouteCandidate(
        stops=stops,
        total_minutes=total_minutes,
        total_cost=round(total_cost, 2),
        utility=utility,
        skipped_count=skipped_count,
        fitness_score=round(fitness, 3),
    )


def _dominates(left: RouteCandidate, right: RouteCandidate) -> bool:
    """Return true when left Pareto-dominates right."""
    no_worse = (
        left.utility >= right.utility
        and left.total_minutes <= right.total_minutes
        and left.total_cost <= right.total_cost
        and left.skipped_count <= right.skipped_count
    )
    strictly_better = (
        left.utility > right.utility
        or left.total_minutes < right.total_minutes
        or left.total_cost < right.total_cost
        or left.skipped_count < right.skipped_count
    )
    return no_worse and strictly_better


def _pareto_front(candidates: list[RouteCandidate]) -> list[RouteCandidate]:
    return [
        candidate
        for candidate in candidates
        if not any(other is not candidate and _dominates(other, candidate) for other in candidates)
    ]


def _solve_day_route(
    day: int,
    hotel: POICandidate,
    cluster: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    day_end: str,
    constraints: list[WeatherConstraint],
) -> DayRoute:
    population = [
        _evaluate_sequence(hotel, sequence, matrix, day_start, day_end, constraints)
        for sequence in _candidate_sequences(hotel, cluster, matrix, day_start, constraints)
    ]
    if not population:
        route = DayRoute(day=day, stops=[], total_minutes=0, total_cost=0, fitness_score=0)
        return attach_route_geometry(hotel, route)

    front = _pareto_front(population)
    best = max(
        front,
        key=lambda candidate: (
            candidate.fitness_score,
            len(candidate.stops),
            candidate.utility,
            -candidate.total_minutes,
            -candidate.total_cost,
        ),
    )
    route = DayRoute(
        day=day,
        stops=[stop.model_copy(update={"day": day}) for stop in best.stops],
        total_minutes=best.total_minutes,
        total_cost=best.total_cost,
        fitness_score=best.fitness_score,
    )
    return attach_route_geometry(hotel, route)


def solve_routes(
    hotel: POICandidate,
    pois: list[POICandidate],
    days: int,
    matrix: dict[str, MatrixEdge],
    day_start: str = "09:00",
    day_end: str | None = None,
    weather_constraints: list[WeatherConstraint] | None = None,
) -> list[DayRoute]:
    """Solve capacity-clustered TD-VRPTW routes with multi-objective search.

    Phase one clusters POIs by space and day capacity. Phase two evaluates a
    compact deterministic population per day and chooses a Pareto-efficient
    route under time-window and weather constraints.
    """
    clusters = cluster_by_day(pois, days)
    constraints = weather_constraints or []
    horizon = day_end or day_end_from_start(day_start)
    return [
        _solve_day_route(
            day=day_index,
            hotel=hotel,
            cluster=cluster,
            matrix=matrix,
            day_start=day_start,
            day_end=horizon,
            constraints=constraints,
        )
        for day_index, cluster in enumerate(clusters, start=1)
    ]


def cheapest_insertion(
    route: DayRoute,
    new_poi: POICandidate,
    hotel: POICandidate,
    matrix: dict[str, MatrixEdge],
    day_start: str = "09:00",
    day_end: str | None = None,
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
        day_end=day_end,
        weather_constraints=weather_constraints,
    )[0]
