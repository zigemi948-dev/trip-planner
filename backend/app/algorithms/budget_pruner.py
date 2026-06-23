from __future__ import annotations

from app.graph.state import BudgetRepairAction, POICandidate


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
    """Return a new POI list with one budget-heavy candidate removed."""
    candidate = choose_budget_prune_candidate(pois)
    if candidate is None:
        return pois, None

    action = BudgetRepairAction(
        removed_poi_id=candidate.id,
        removed_poi_name=candidate.name,
        reason="removed lowest utility-per-cost paid POI to satisfy budget",
    )
    return [poi for poi in pois if poi.id != candidate.id], action
