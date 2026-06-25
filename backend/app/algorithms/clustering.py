from __future__ import annotations

from app.core.config import settings
from app.algorithms.geo import haversine_km
from app.graph.state import Coordinates, POICandidate


COMMUTE_PADDING_MINUTES = 30
KMEANS_ITERATIONS = 6


def cluster_by_day(
    pois: list[POICandidate],
    days: int,
    max_day_minutes: int | None = None,
    max_day_fixed_cost: float | None = None,
) -> list[list[POICandidate]]:
    """Capacity-aware spatial clustering for the first heuristic phase.

    The PRD calls for a K-Means/DBSCAN-style reduction before day-level route
    solving. This deterministic variant avoids extra dependencies while still
    considering physical coordinates and a hard daily capacity budget.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if not pois:
        return [[] for _ in range(days)]

    limit = max_day_minutes or settings.max_day_minutes
    ordered = sorted(pois, key=lambda poi: (poi.coordinates.lng, poi.coordinates.lat, -poi.utility))
    centroids = _initial_centroids(ordered, days)
    clusters: list[list[POICandidate]] = [[] for _ in range(days)]

    for _ in range(KMEANS_ITERATIONS):
        clusters = _assign_with_capacity(ordered, centroids, limit, max_day_fixed_cost)
        centroids = _recompute_centroids(clusters, centroids)

    # Stable final ordering: high utility first inside each spatial day bucket.
    for cluster in clusters:
        cluster.sort(key=lambda poi: (-poi.utility, poi.coordinates.lng, poi.coordinates.lat))

    return clusters


def _initial_centroids(pois: list[POICandidate], days: int) -> list[tuple[float, float]]:
    centroids: list[tuple[float, float]] = []
    for index in range(days):
        sample_index = round(index * (len(pois) - 1) / max(days - 1, 1))
        sample = pois[sample_index]
        centroids.append((sample.coordinates.lat, sample.coordinates.lng))
    return centroids


def _assign_with_capacity(
    pois: list[POICandidate],
    centroids: list[tuple[float, float]],
    limit: int,
    max_day_fixed_cost: float | None = None,
) -> list[list[POICandidate]]:
    clusters: list[list[POICandidate]] = [[] for _ in centroids]
    used_minutes = [0 for _ in centroids]
    used_fixed_cost = [0.0 for _ in centroids]

    # Assign costly/high-value POIs first so the hard capacity has first claim
    # on the most important stops.
    assignment_order = sorted(
        pois,
        key=lambda poi: (-poi.visit_duration_minutes, -poi.utility, poi.coordinates.lng),
    )
    for poi in assignment_order:
        demand = _capacity_demand(poi)
        feasible_days = [
            index
            for index in range(len(centroids))
            if used_minutes[index] + demand <= limit
            and _within_day_cost_budget(
                used_fixed_cost[index],
                poi.fixed_cost,
                max_day_fixed_cost,
            )
        ]
        candidate_days = feasible_days or list(range(len(centroids)))
        best_day = min(
            candidate_days,
            key=lambda index: (
                _cost_overage(used_fixed_cost[index], poi.fixed_cost, max_day_fixed_cost),
                _centroid_distance_km(poi, centroids[index]),
                used_minutes[index],
                len(clusters[index]),
            ),
        )
        clusters[best_day].append(poi)
        used_minutes[best_day] += demand
        used_fixed_cost[best_day] += poi.fixed_cost
    return clusters


def _recompute_centroids(
    clusters: list[list[POICandidate]],
    previous: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    centroids: list[tuple[float, float]] = []
    for index, cluster in enumerate(clusters):
        if not cluster:
            centroids.append(previous[index])
            continue
        total_weight = sum(max(poi.utility, 1.0) for poi in cluster)
        lat = sum(poi.coordinates.lat * max(poi.utility, 1.0) for poi in cluster) / total_weight
        lng = sum(poi.coordinates.lng * max(poi.utility, 1.0) for poi in cluster) / total_weight
        centroids.append((lat, lng))
    return centroids


def _capacity_demand(poi: POICandidate) -> int:
    return poi.visit_duration_minutes + COMMUTE_PADDING_MINUTES


def _within_day_cost_budget(
    current_cost: float,
    added_cost: float,
    max_day_fixed_cost: float | None,
) -> bool:
    if max_day_fixed_cost is None or max_day_fixed_cost <= 0:
        return True
    return current_cost + added_cost <= max_day_fixed_cost


def _cost_overage(
    current_cost: float,
    added_cost: float,
    max_day_fixed_cost: float | None,
) -> float:
    if max_day_fixed_cost is None or max_day_fixed_cost <= 0:
        return 0.0
    return max(0.0, current_cost + added_cost - max_day_fixed_cost)


def _centroid_distance_km(poi: POICandidate, centroid: tuple[float, float]) -> float:
    return haversine_km(poi.coordinates, Coordinates(lat=centroid[0], lng=centroid[1]))
