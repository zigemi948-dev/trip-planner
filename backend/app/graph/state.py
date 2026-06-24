from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TextEnum(str, Enum):
    """Python 3.10 compatible string enum base.

    The standard-library string enum helper exists only in Python 3.11+, while
    many Anaconda project environments still run Python 3.10. Inheriting from `str` keeps JSON
    serialization and string comparisons convenient without requiring 3.11.
    """

    def __str__(self) -> str:
        return self.value


class TransportMode(TextEnum):
    driving = "Driving"
    transit = "Transit"
    walking = "Walking"


class Coordinates(BaseModel):
    """Validated geographic coordinate used by every spatial algorithm."""

    lat: float
    lng: float

    @field_validator("lat")
    @classmethod
    def validate_latitude(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @field_validator("lng")
    @classmethod
    def validate_longitude(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return value


class BoundingBox(BaseModel):
    """Minimal map extent that can frame a route geometry."""

    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


class IntentConstraints(BaseModel):
    """User intent after natural-language parsing.

    This is the contract between LLM-facing agents and deterministic compute
    nodes. Route solvers should read only this normalized structure.
    """

    user_query: str
    destination: str
    days: int = Field(default=1, ge=1, le=14)
    time_window_baseline: tuple[str, str] = ("09:00", "19:00")
    budget_limit: float = Field(default=800.0, gt=0)
    preferences: list[str] = Field(default_factory=list)


class IntentParseRequest(BaseModel):
    """Raw natural-language planning request submitted by the UI."""

    user_query: str


class RuntimeCapabilities(BaseModel):
    """Runtime integration switches exposed for UI diagnostics."""

    provider_mode: str
    amap_configured: bool
    amap_enabled: bool
    amap_mcp_configured: bool = False
    mcp_inprocess_allowed: bool = False
    llm_configured: bool
    llm_enabled: bool
    llm_model: str
    fallback_mode: bool


class IntegrationProbeResult(BaseModel):
    """One external integration probe result without exposing secrets."""

    name: str
    status: str
    enabled: bool
    message: str


class IntegrationProbeResponse(BaseModel):
    """Manual smoke-test result for external integrations."""

    results: list[IntegrationProbeResult] = Field(default_factory=list)


class WorkflowTopologyNode(BaseModel):
    """One graph node exposed for workflow topology diagnostics."""

    name: str
    phase: str
    description: str


class WorkflowTopologyEdge(BaseModel):
    """One directed or conditional edge in the workflow topology."""

    source: str
    target: str
    condition: str = "always"


class WorkflowTopology(BaseModel):
    """Map-Compute-Reduce graph shape for UI and diagnostics."""

    runtime: str = "langgraph"
    nodes: list[WorkflowTopologyNode] = Field(default_factory=list)
    edges: list[WorkflowTopologyEdge] = Field(default_factory=list)


class WeatherConstraint(BaseModel):
    """Weather or operating constraint that can block some POI categories."""

    time_window: tuple[str, str]
    rule: str
    day: int | None = None
    blocked_categories: list[str] = Field(default_factory=list)
    block_outdoor: bool = False
    reason: str = ""


class DailyWeatherForecast(BaseModel):
    """User-facing weather forecast for one itinerary day."""

    day: int
    date: str = ""
    weather: str = ""
    temperature_min: float | None = None
    temperature_max: float | None = None
    wind: str = ""
    advisory: str = ""
    source: str = "fallback"


class WeatherReport(BaseModel):
    """Weather agent output consumed by both solver and planner renderer."""

    constraints: list[WeatherConstraint] = Field(default_factory=list)
    forecasts: list[DailyWeatherForecast] = Field(default_factory=list)


class FinancialContext(BaseModel):
    """City-level financial assumptions used by budget evaluation."""

    currency: str = "CNY"
    exchange_rate: float = 1.0
    base_transit_fare: float = 4.0
    driving_rate_per_km: float = 2.6
    avg_meal_cost: float = 45.0
    avg_hotel_nightly_cost: float = 80.0


class POICandidate(BaseModel):
    """A candidate node that can enter the route computation graph."""

    id: str
    name: str
    category: str
    coordinates: Coordinates
    fixed_cost: float = Field(default=0, ge=0)
    visit_duration_minutes: int = Field(default=90, ge=0, le=480)
    utility: float = Field(default=1.0, ge=0)
    opening_window: tuple[str, str] = ("09:00", "18:00")
    indoor: bool = False


class MatrixEdge(BaseModel):
    """Directed travel edge between two POI nodes for one time slice."""

    origin_id: str
    destination_id: str
    hour: int = Field(default=9, ge=0, le=23)
    distance_km: float
    duration_minutes: int
    mode: TransportMode
    cost: float
    boarding_station: str = ""
    alighting_station: str = ""
    transit_note: str = ""


class SpatialGraphData(BaseModel):
    """All spatial data produced by Map agents and matrix builders."""

    hotel_anchor: POICandidate | None = None
    poi_candidates: list[POICandidate] = Field(default_factory=list)
    time_dependent_tensor: dict[str, MatrixEdge] = Field(default_factory=dict)
    weather_constraints: list[WeatherConstraint] = Field(default_factory=list)
    weather_forecast: list[DailyWeatherForecast] = Field(default_factory=list)


class RouteStop(BaseModel):
    """A concrete visit on a day route after solver ordering."""

    poi: POICandidate
    day: int
    arrival_time: str
    departure_time: str
    inbound_mode: TransportMode | None = None
    inbound_cost: float = 0
    inbound_distance_km: float = 0
    inbound_boarding_station: str = ""
    inbound_alighting_station: str = ""
    inbound_transit_note: str = ""


class DayCostBreakdown(BaseModel):
    """Per-day cost detail for route display and budget diagnostics."""

    day: int
    ticket_cost: float = 0
    transport_cost: float = 0
    food_cost: float = 0
    accommodation_cost: float = 0
    total_cost: float = 0


class DayRoute(BaseModel):
    """Optimized route fragment for a single travel day."""

    day: int
    stops: list[RouteStop]
    total_minutes: int
    total_cost: float
    fitness_score: float
    cost_breakdown: DayCostBreakdown | None = None
    geometry: list[Coordinates] = Field(default_factory=list)
    bounds: BoundingBox | None = None


class BudgetBreakdown(BaseModel):
    """Budget line items calculated from the solved route graph."""

    fixed_cost: float = 0
    transport_cost: float = 0
    food_cost: float = 0
    accommodation_cost: float = 0
    total_cost: float = 0
    budget_limit: float = 0
    remaining: float = 0


class BudgetRepairAction(BaseModel):
    """One automatic pruning action taken to satisfy the budget red line."""

    removed_poi_id: str
    removed_poi_name: str
    reason: str


class FitnessPoint(BaseModel):
    """One point in the solver fitness curve shown to the frontend."""

    epoch: int
    label: str
    score: float


class RouteQualityMetrics(BaseModel):
    """Aggregated observability metrics for one solved route."""

    total_stops: int = 0
    total_distance_km: float = 0
    total_minutes: int = 0
    total_transport_cost: float = 0
    budget_usage_ratio: float = 0
    average_fitness: float = 0
    mode_share: dict[str, int] = Field(default_factory=dict)


class HotelStay(BaseModel):
    """Daily hotel stay information shown in the planner output."""

    day: int
    hotel: POICandidate
    check_in_time: str
    check_out_time: str
    note: str = ""


class RoutingSolution(BaseModel):
    """Final route, budget, warnings, and human-readable rendering."""

    optimized_route: list[DayRoute] = Field(default_factory=list)
    budget_breakdown: BudgetBreakdown = Field(default_factory=BudgetBreakdown)
    daily_costs: list[DayCostBreakdown] = Field(default_factory=list)
    hotel_anchor: POICandidate | None = None
    hotel_stays: list[HotelStay] = Field(default_factory=list)
    daily_weather: list[DailyWeatherForecast] = Field(default_factory=list)
    narrative: str = ""
    warnings: list[str] = Field(default_factory=list)
    repair_actions: list[BudgetRepairAction] = Field(default_factory=list)
    quality_metrics: RouteQualityMetrics = Field(default_factory=RouteQualityMetrics)
    fitness_curve: list[FitnessPoint] = Field(default_factory=list)


class GraphStatus(TextEnum):
    created = "created"
    mapped = "mapped"
    matrix_ready = "matrix_ready"
    solved = "solved"
    replanned = "replanned"
    budget_checked = "budget_checked"
    budget_repaired = "budget_repaired"
    compressed = "compressed"
    rendered = "rendered"
    failed = "failed"


class GraphControls(BaseModel):
    """Workflow cursor and event stream for UI/debug visibility."""

    current_status: GraphStatus = GraphStatus.created
    current_node: str | None = None
    current_phase: str | None = None
    repair_attempts: int = 0
    edit_trigger: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class TripState(BaseModel):
    """Single source of truth passed through the whole planning workflow."""

    intent_constraints: IntentConstraints
    financial_context: FinancialContext = Field(default_factory=FinancialContext)
    spatial_graph_data: SpatialGraphData = Field(default_factory=SpatialGraphData)
    routing_solution: RoutingSolution = Field(default_factory=RoutingSolution)
    graph_controls: GraphControls = Field(default_factory=GraphControls)

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Append a lightweight event for WebSocket streaming or debugging."""
        self.graph_controls.events.append({"event": event, "payload": payload or {}})


class ReplanRequest(BaseModel):
    """Request for local route repair after a user edits the itinerary."""

    state: TripState
    day: int = Field(ge=1)
    new_poi: POICandidate


class ExportRequest(BaseModel):
    """Request to render a route solution into a portable payload."""

    solution: RoutingSolution
    export_format: str = Field(default="html", pattern="^(html|pdf|png)$")


class JobStatus(TextEnum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"


class PlanningJob(BaseModel):
    """Stored planning job for polling and event replay."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    status: JobStatus = JobStatus.queued
    intent: IntentConstraints
    state: TripState | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


class PlanningJobSummary(BaseModel):
    """Small job shape for list endpoints."""

    id: str
    status: JobStatus
    destination: str
    days: int
    event_count: int = 0


class PlanningJobEvents(BaseModel):
    """Incremental event window for polling clients."""

    job_id: str
    status: JobStatus
    events: list[dict[str, Any]] = Field(default_factory=list)
    next_offset: int = 0
    state: TripState | None = None
