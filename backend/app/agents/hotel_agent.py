from app.graph.state import Coordinates, IntentConstraints, POICandidate


def resolve_hotel_anchor(intent: IntentConstraints) -> POICandidate:
    """Resolve the route's start/end anchor.

    Hotels are modeled as POI nodes with zero visit duration so they can share
    the same matrix and graph contracts as attractions.
    """
    return POICandidate(
        id="hotel_anchor",
        name=f"{intent.destination} Central Hotel",
        category="hotel",
        coordinates=Coordinates(lat=31.2328, lng=121.4752),
        fixed_cost=0,
        visit_duration_minutes=0,
        utility=0,
        indoor=True,
    )
