from __future__ import annotations

from app.core.config import settings
from app.graph.state import POICandidate


def cluster_by_day(
    pois: list[POICandidate],
    days: int,
    max_day_minutes: int | None = None,
) -> list[list[POICandidate]]:
    """Capacity-aware deterministic clustering.

    This is the first production-safe fallback before introducing heavier
    K-Means/DBSCAN dependencies. It keeps the capacity constraint explicit.
    """
    if days <= 0:
        raise ValueError("days must be positive")

    limit = max_day_minutes or settings.max_day_minutes
    clusters: list[list[POICandidate]] = [[] for _ in range(days)]
    used_minutes = [0 for _ in range(days)]

    # Put high-value POIs first, then distribute them to the lightest day.
    # This keeps the fallback deterministic and prevents one day from absorbing
    # all premium attractions before the real clustering solver is introduced.
    sorted_pois = sorted(
        pois,
        key=lambda poi: (-poi.utility, poi.coordinates.lng, poi.coordinates.lat),
    )

    for poi in sorted_pois:
        best_day = min(range(days), key=lambda day: (used_minutes[day], len(clusters[day])))
        if used_minutes[best_day] + poi.visit_duration_minutes > limit:
            # The hard cap is best-effort in this fallback; later iterations can
            # replace this with capacity-constrained K-Means/DBSCAN.
            best_day = min(range(days), key=lambda day: used_minutes[day])
        clusters[best_day].append(poi)
        used_minutes[best_day] += poi.visit_duration_minutes

    return clusters
