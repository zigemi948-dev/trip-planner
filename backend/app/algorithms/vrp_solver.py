from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import permutations
from random import Random
from typing import Callable

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
MAX_EXACT_PERMUTATION_SIZE = 5
NSGA_POPULATION_SIZE = 32
NSGA_GENERATIONS = 14
NSGA_MUTATION_RATE = 0.22
TIME_PENALTY_WEIGHT = 1 / 600
COST_PENALTY_WEIGHT = 1 / 500
SolverProgressCallback = Callable[[int, int, float], None]


@dataclass(frozen=True)
class RouteCandidate:
    """One evaluated day-level TD-VRPTW candidate."""

    stops: list[RouteStop]
    total_minutes: int
    total_cost: float
    utility: float
    skipped_count: int
    fitness_score: float


@dataclass
class NSGAIndividual:
    """One NSGA-II chromosome with evaluated route objectives."""

    sequence: list[POICandidate]
    candidate: RouteCandidate
    rank: int = 0
    crowding_distance: float = 0.0


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
    """Build a deterministic seed population for NSGA-II."""
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
    for sequence in base_sequences:
        _add_unique_sequence(sequences, list(sequence))

    if len(pois) <= MAX_EXACT_PERMUTATION_SIZE:
        for sequence in permutations(pois):
            _add_unique_sequence(sequences, list(sequence))

    rng = _rng_for_pois(pois, day_start)
    attempts = 0
    max_attempts = NSGA_POPULATION_SIZE * 6
    while len(sequences) < NSGA_POPULATION_SIZE and attempts < max_attempts:
        seed = list(sequences[len(sequences) % len(sequences)])
        _mutate_sequence(seed, rng, force=True)
        _add_unique_sequence(sequences, seed)
        attempts += 1

    return sequences[:NSGA_POPULATION_SIZE]


def _add_unique_sequence(sequences: list[list[POICandidate]], sequence: list[POICandidate]) -> None:
    key = _sequence_key(sequence)
    if key not in {_sequence_key(item) for item in sequences}:
        sequences.append(sequence)


def _rng_for_pois(pois: list[POICandidate], salt: str = "") -> Random:
    seed_text = "|".join(sorted(poi.id for poi in pois)) + f"|{salt}"
    return Random(seed_text)


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
                inbound_boarding_station=inbound.boarding_station if inbound else "",
                inbound_alighting_station=inbound.alighting_station if inbound else "",
                inbound_transit_note=inbound.transit_note if inbound else "",
                inbound_geometry=inbound.polyline if inbound else [],
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


def _run_nsga2(
    hotel: POICandidate,
    cluster: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    day_end: str,
    constraints: list[WeatherConstraint],
    day: int = 1,
    progress_callback: SolverProgressCallback | None = None,
) -> list[NSGAIndividual]:
    """Run deterministic NSGA-II for one day-level TD-VRPTW cluster."""
    sequences = _candidate_sequences(hotel, cluster, matrix, day_start, constraints)
    if not sequences:
        return []

    rng = _rng_for_pois(cluster, f"{day_start}-{day_end}")
    population = _evaluate_population(hotel, sequences, matrix, day_start, day_end, constraints)
    population = _select_next_generation(population, NSGA_POPULATION_SIZE)
    _emit_solver_progress(progress_callback, day, 0, population)

    for generation in range(1, NSGA_GENERATIONS + 1):
        parents = _rank_population(population)
        offspring_sequences: list[list[POICandidate]] = []
        attempts = 0
        max_attempts = NSGA_POPULATION_SIZE * 6
        while len(offspring_sequences) < NSGA_POPULATION_SIZE and attempts < max_attempts:
            left = _tournament_select(parents, rng)
            right = _tournament_select(parents, rng)
            child = _order_crossover(left.sequence, right.sequence, rng)
            _mutate_sequence(child, rng)
            _add_unique_sequence(offspring_sequences, child)
            attempts += 1
        offspring = _evaluate_population(hotel, offspring_sequences, matrix, day_start, day_end, constraints)
        population = _select_next_generation([*population, *offspring], NSGA_POPULATION_SIZE)
        _emit_solver_progress(progress_callback, day, generation, population)

    return _rank_population(population)


def _emit_solver_progress(
    progress_callback: SolverProgressCallback | None,
    day: int,
    epoch: int,
    population: list[NSGAIndividual],
) -> None:
    if progress_callback is None or not population:
        return
    best = max(item.candidate.fitness_score for item in population)
    progress_callback(day, epoch, round(best, 3))


def _evaluate_population(
    hotel: POICandidate,
    sequences: list[list[POICandidate]],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    day_end: str,
    constraints: list[WeatherConstraint],
) -> list[NSGAIndividual]:
    seen: set[tuple[str, ...]] = set()
    individuals: list[NSGAIndividual] = []
    for sequence in sequences:
        key = _sequence_key(sequence)
        if key in seen:
            continue
        seen.add(key)
        individuals.append(
            NSGAIndividual(
                sequence=list(sequence),
                candidate=_evaluate_sequence(hotel, sequence, matrix, day_start, day_end, constraints),
            )
        )
    return individuals


def _rank_population(individuals: list[NSGAIndividual]) -> list[NSGAIndividual]:
    fronts = _non_dominated_sort(individuals)
    ranked: list[NSGAIndividual] = []
    for rank, front in enumerate(fronts):
        for individual in front:
            individual.rank = rank
        _assign_crowding_distance(front)
        ranked.extend(front)
    return sorted(ranked, key=lambda item: (item.rank, -item.crowding_distance, -item.candidate.fitness_score))


def _select_next_generation(individuals: list[NSGAIndividual], population_size: int) -> list[NSGAIndividual]:
    selected: list[NSGAIndividual] = []
    for front in _non_dominated_sort(individuals):
        _assign_crowding_distance(front)
        if len(selected) + len(front) <= population_size:
            selected.extend(front)
            continue
        remaining = population_size - len(selected)
        selected.extend(
            sorted(front, key=lambda item: (-item.crowding_distance, -item.candidate.fitness_score))[:remaining]
        )
        break
    return _rank_population(selected)


def _non_dominated_sort(individuals: list[NSGAIndividual]) -> list[list[NSGAIndividual]]:
    domination_counts: dict[int, int] = {index: 0 for index in range(len(individuals))}
    dominated_sets: dict[int, list[int]] = {index: [] for index in range(len(individuals))}
    fronts: list[list[int]] = [[]]

    for left_index, left in enumerate(individuals):
        for right_index, right in enumerate(individuals):
            if left_index == right_index:
                continue
            if _dominates(left.candidate, right.candidate):
                dominated_sets[left_index].append(right_index)
            elif _dominates(right.candidate, left.candidate):
                domination_counts[left_index] += 1
        if domination_counts[left_index] == 0:
            fronts[0].append(left_index)

    front_index = 0
    while front_index < len(fronts) and fronts[front_index]:
        next_front: list[int] = []
        for left_index in fronts[front_index]:
            for right_index in dominated_sets[left_index]:
                domination_counts[right_index] -= 1
                if domination_counts[right_index] == 0:
                    next_front.append(right_index)
        if next_front:
            fronts.append(next_front)
        front_index += 1

    return [[individuals[index] for index in front] for front in fronts if front]


def _assign_crowding_distance(front: list[NSGAIndividual]) -> None:
    if not front:
        return
    for individual in front:
        individual.crowding_distance = 0.0
    if len(front) <= 2:
        for individual in front:
            individual.crowding_distance = float("inf")
        return

    objective_getters = [
        lambda item: item.candidate.utility,
        lambda item: -item.candidate.total_minutes,
        lambda item: -item.candidate.total_cost,
        lambda item: -item.candidate.skipped_count,
    ]
    for getter in objective_getters:
        ordered = sorted(front, key=getter)
        ordered[0].crowding_distance = float("inf")
        ordered[-1].crowding_distance = float("inf")
        minimum = getter(ordered[0])
        maximum = getter(ordered[-1])
        if maximum == minimum:
            continue
        for index in range(1, len(ordered) - 1):
            if ordered[index].crowding_distance == float("inf"):
                continue
            ordered[index].crowding_distance += (getter(ordered[index + 1]) - getter(ordered[index - 1])) / (
                maximum - minimum
            )


def _tournament_select(population: list[NSGAIndividual], rng: Random) -> NSGAIndividual:
    left = population[rng.randrange(len(population))]
    right = population[rng.randrange(len(population))]
    if (left.rank, -left.crowding_distance, -left.candidate.fitness_score) < (
        right.rank,
        -right.crowding_distance,
        -right.candidate.fitness_score,
    ):
        return left
    return right


def _order_crossover(
    left: list[POICandidate],
    right: list[POICandidate],
    rng: Random,
) -> list[POICandidate]:
    if len(left) <= 2:
        return list(left)
    start = rng.randrange(0, len(left) - 1)
    end = rng.randrange(start + 1, len(left))
    child: list[POICandidate | None] = [None for _ in left]
    child[start : end + 1] = left[start : end + 1]
    used = {poi.id for poi in child if poi is not None}
    fill_values = [poi for poi in right if poi.id not in used]
    fill_index = 0
    for index, value in enumerate(child):
        if value is None:
            child[index] = fill_values[fill_index]
            fill_index += 1
    return [poi for poi in child if poi is not None]


def _mutate_sequence(sequence: list[POICandidate], rng: Random, force: bool = False) -> None:
    if len(sequence) < 2:
        return
    if force or rng.random() < NSGA_MUTATION_RATE:
        left = rng.randrange(len(sequence))
        right = rng.randrange(len(sequence))
        sequence[left], sequence[right] = sequence[right], sequence[left]
    if len(sequence) > 3 and (force or rng.random() < NSGA_MUTATION_RATE / 2):
        start = rng.randrange(0, len(sequence) - 2)
        end = rng.randrange(start + 1, len(sequence))
        sequence[start : end + 1] = reversed(sequence[start : end + 1])


def _solve_day_route(
    day: int,
    hotel: POICandidate,
    cluster: list[POICandidate],
    matrix: dict[str, MatrixEdge],
    day_start: str,
    day_end: str,
    constraints: list[WeatherConstraint],
    progress_callback: SolverProgressCallback | None = None,
) -> DayRoute:
    population = _run_nsga2(
        hotel,
        cluster,
        matrix,
        day_start,
        day_end,
        constraints,
        day=day,
        progress_callback=progress_callback,
    )
    if not population:
        route = DayRoute(day=day, stops=[], total_minutes=0, total_cost=0, fitness_score=0)
        return attach_route_geometry(hotel, route)

    front = [individual.candidate for individual in population if individual.rank == 0]
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
    budget_limit: float | None = None,
    progress_callback: SolverProgressCallback | None = None,
) -> list[DayRoute]:
    """Solve capacity-clustered TD-VRPTW routes with multi-objective search.

    Phase one clusters POIs by space and day capacity. Phase two evaluates a
    compact deterministic population per day and chooses a Pareto-efficient
    route under time-window and weather constraints.
    """
    daily_fixed_cost_budget = None
    if budget_limit is not None and budget_limit > 0 and days > 0:
        daily_fixed_cost_budget = budget_limit / days
    clusters = cluster_by_day(pois, days, max_day_fixed_cost=daily_fixed_cost_budget)
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
            progress_callback=progress_callback,
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
