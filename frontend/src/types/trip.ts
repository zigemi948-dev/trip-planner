export type TransportMode = 'Driving' | 'Transit' | 'Walking';

export interface Coordinates {
  lat: number;
  lng: number;
}

export interface BoundingBox {
  min_lat: number;
  min_lng: number;
  max_lat: number;
  max_lng: number;
}

// These interfaces intentionally mirror backend Pydantic response models.
export interface POICandidate {
  id: string;
  name: string;
  category: string;
  coordinates: Coordinates;
  fixed_cost: number;
  visit_duration_minutes: number;
  utility: number;
  opening_window: [string, string];
  indoor: boolean;
}

export interface RouteStop {
  poi: POICandidate;
  day: number;
  arrival_time: string;
  departure_time: string;
  inbound_mode: TransportMode | null;
  inbound_cost: number;
  inbound_distance_km: number;
  inbound_boarding_station: string;
  inbound_alighting_station: string;
  inbound_transit_note: string;
  inbound_geometry?: Coordinates[];
}

export interface DayCostBreakdown {
  day: number;
  ticket_cost: number;
  transport_cost: number;
  food_cost: number;
  accommodation_cost: number;
  total_cost: number;
}

export interface DayRoute {
  day: number;
  stops: RouteStop[];
  total_minutes: number;
  total_cost: number;
  fitness_score: number;
  cost_breakdown: DayCostBreakdown | null;
  geometry: Coordinates[];
  bounds: BoundingBox | null;
}

export interface BudgetBreakdown {
  fixed_cost: number;
  transport_cost: number;
  food_cost: number;
  accommodation_cost: number;
  total_cost: number;
  budget_limit: number;
  remaining: number;
}

export interface FitnessPoint {
  epoch: number;
  label: string;
  score: number;
}

export interface RouteQualityMetrics {
  total_stops: number;
  total_distance_km: number;
  total_minutes: number;
  total_transport_cost: number;
  budget_usage_ratio: number;
  average_fitness: number;
  mode_share: Record<string, number>;
}

export interface DailyWeatherForecast {
  day: number;
  date: string;
  weather: string;
  temperature_min: number | null;
  temperature_max: number | null;
  wind: string;
  advisory: string;
  source: string;
}

export interface HotelStay {
  day: number;
  hotel: POICandidate;
  check_in_time: string;
  check_out_time: string;
  note: string;
}

export interface RoutingSolution {
  optimized_route: DayRoute[];
  budget_breakdown: BudgetBreakdown;
  daily_costs: DayCostBreakdown[];
  hotel_anchor: POICandidate | null;
  hotel_stays: HotelStay[];
  daily_weather: DailyWeatherForecast[];
  narrative: string;
  warnings: string[];
  repair_actions: Array<{
    removed_poi_id: string;
    removed_poi_name: string;
    reason: string;
  }>;
  quality_metrics: RouteQualityMetrics;
  fitness_curve: FitnessPoint[];
}

export interface TripState {
  // The UI currently consumes the solution and graph events; other backend
  // fields can be added here as screens grow.
  routing_solution: RoutingSolution;
  graph_controls: {
    current_status: string;
    events: Array<{ event: string; payload: Record<string, unknown> }>;
  };
}

export interface WorkflowEvent {
  event: string;
  payload: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
}

export interface RuntimeCapabilities {
  provider_mode: string;
  amap_configured: boolean;
  amap_enabled: boolean;
  llm_configured: boolean;
  llm_enabled: boolean;
  llm_model: string;
  fallback_mode: boolean;
}

export interface IntegrationProbeResult {
  name: string;
  status: string;
  enabled: boolean;
  message: string;
}

export interface IntegrationProbeResponse {
  results: IntegrationProbeResult[];
}

export interface IntentConstraints {
  user_query: string;
  destination: string;
  days: number;
  time_window_baseline: [string, string];
  budget_limit: number;
  preferences: string[];
}

export interface ReplanRequest {
  state: TripState;
  day: number;
  new_poi: POICandidate;
}

export type JobStatus = 'queued' | 'running' | 'complete' | 'failed';

export interface PlanningJob {
  id: string;
  status: JobStatus;
  intent: {
    user_query: string;
    destination: string;
    days: number;
    budget_limit: number;
    preferences: string[];
  };
  state: TripState | null;
  events: Array<{ event: string; payload: Record<string, unknown> }>;
  error: string;
}

export interface PlanningJobSummary {
  id: string;
  status: JobStatus;
  destination: string;
  days: number;
  event_count: number;
}

export interface PlanningJobEvents {
  job_id: string;
  status: JobStatus;
  events: WorkflowEvent[];
  next_offset: number;
  state: TripState | null;
}
