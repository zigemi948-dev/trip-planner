class TripPlannerError(Exception):
    """Base class for domain errors."""


class MatrixBuildError(TripPlannerError):
    """Raised when the road matrix cannot be built."""


class SolverTimeoutError(TripPlannerError):
    """Raised when a route solver exceeds its time budget."""


class BudgetExceededError(TripPlannerError):
    """Raised when a route violates the user budget."""


class InvalidPOIError(TripPlannerError):
    """Raised when a POI cannot enter the computation graph."""


class ErrorResponse:
    """Error code constants used by API exception handlers."""

    VALIDATION_ERROR = "validation_error"
    DOMAIN_ERROR = "trip_planner_error"
    INTERNAL_ERROR = "internal_error"
