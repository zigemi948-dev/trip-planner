from __future__ import annotations

from app.graph.state import BudgetRepairAction, POICandidate


def choose_budget_replacement(
    candidate: POICandidate,
    pois: list[POICandidate],
) -> POICandidate | None:
    """Pick a cheaper same-category alternative that can remain after pruning."""
    alternatives = [
        poi
        for poi in pois
        if poi.id != candidate.id
        and poi.category == candidate.category
        and poi.fixed_cost < candidate.fixed_cost
    ]
    if not alternatives:
        return None
    return max(
        alternatives,
        key=lambda poi: (
            poi.utility / max(poi.fixed_cost, 1),
            poi.utility,
            -poi.fixed_cost,
        ),
    )


def choose_budget_prune_candidate(pois: list[POICandidate]) -> POICandidate | None:
    """Pick the least valuable paid POI to remove during budget repair."""
    paid_pois = [poi for poi in pois if poi.fixed_cost > 0]
    if not paid_pois:
        return None

    return min(
        paid_pois,
        key=lambda poi: (
            poi.utility / max(poi.fixed_cost, 1),
            poi.utility,
            -poi.fixed_cost,
        ),
    )


def remove_budget_candidate(pois: list[POICandidate]) -> tuple[list[POICandidate], BudgetRepairAction | None]:
    """Return a new POI list after removing or replacing one budget-heavy candidate."""
    candidate = choose_budget_prune_candidate(pois)
    if candidate is None:
        return pois, None

    replacement = choose_budget_replacement(candidate, pois)
    if replacement is not None:
        action = BudgetRepairAction(
            removed_poi_id=candidate.id,
            removed_poi_name=candidate.name,
            replacement_poi_id=replacement.id,
            replacement_poi_name=replacement.name,
            reason=(
                "replaced lowest utility-per-cost paid POI with a cheaper "
                f"{replacement.category} alternative to satisfy budget"
            ),
        )
        return [poi for poi in pois if poi.id != candidate.id], action

    action = BudgetRepairAction(
        removed_poi_id=candidate.id,
        removed_poi_name=candidate.name,
        reason="removed lowest utility-per-cost paid POI to satisfy budget",
    )
    return [poi for poi in pois if poi.id != candidate.id], action
