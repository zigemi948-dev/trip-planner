import { defineStore } from 'pinia';
import {
  checkHealth,
  exportTrip,
  exportTripFile,
  listPlanningJobs,
  loadRuntimeCapabilities,
  loadDemoTrip,
  parseIntent,
  planTrip,
  probeIntegrations,
  replanTrip,
  streamTripPlan,
  submitPlanningJob,
  type PlanTripRequest
} from '../api/trips';
import type {
  HealthResponse,
  IntegrationProbeResponse,
  PlanningJob,
  PlanningJobSummary,
  POICandidate,
  RuntimeCapabilities,
  TripState,
  WorkflowEvent
} from '../types/trip';

export const useTripStore = defineStore('trip', {
  state: () => ({
    // Keep the backend TripState intact so components stay aligned with Pydantic.
    trip: null as TripState | null,
    loading: false,
    error: '',
    exportPayload: null as Record<string, string> | null,
    activeJob: null as PlanningJob | null,
    jobs: [] as PlanningJobSummary[],
    health: null as HealthResponse | null,
    capabilities: null as RuntimeCapabilities | null,
    integrationProbe: null as IntegrationProbeResponse | null,
    probingIntegrations: false,
    parsedIntent: null as PlanTripRequest | null,
    streamEvents: [] as WorkflowEvent[],
    streaming: false
  }),
  actions: {
    async checkBackend() {
      try {
        this.health = await checkHealth();
        this.capabilities = await loadRuntimeCapabilities();
      } catch (error) {
        this.health = null;
        this.capabilities = null;
        this.error = error instanceof Error ? error.message : 'Unknown error';
      }
    },
    async refreshCapabilities() {
      try {
        this.capabilities = await loadRuntimeCapabilities();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      }
    },
    async probeRuntimeIntegrations() {
      this.probingIntegrations = true;
      this.error = '';
      try {
        this.integrationProbe = await probeIntegrations();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.probingIntegrations = false;
      }
    },
    async demo() {
      // Load deterministic backend demo data for the initial workspace state.
      this.loading = true;
      this.error = '';
      try {
        this.trip = await loadDemoTrip();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async parseRequest(userQuery: string) {
      this.loading = true;
      this.error = '';
      try {
        const parsed = await parseIntent(userQuery);
        this.parsedIntent = {
          user_query: parsed.user_query,
          destination: parsed.destination,
          days: parsed.days,
          time_window_baseline: parsed.time_window_baseline,
          budget_limit: parsed.budget_limit,
          preferences: parsed.preferences
        };
        return this.parsedIntent;
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
        return null;
      } finally {
        this.loading = false;
      }
    },
    async plan(payload: PlanTripRequest) {
      // Submit user-edited constraints and replace the whole solution atomically.
      this.loading = true;
      this.error = '';
      try {
        this.trip = await planTrip(payload);
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async streamPlan(payload: PlanTripRequest) {
      this.loading = true;
      this.streaming = true;
      this.error = '';
      this.streamEvents = [];
      try {
        this.trip = await streamTripPlan(payload, (event) => {
          this.streamEvents.push(event);
        });
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.streaming = false;
        this.loading = false;
      }
    },
    async insertPoi(day: number, newPoi: POICandidate) {
      if (!this.trip) {
        return;
      }
      this.loading = true;
      this.error = '';
      try {
        this.trip = await replanTrip({
          state: this.trip,
          day,
          new_poi: newPoi
        });
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async exportCurrent() {
      if (!this.trip) {
        return;
      }
      this.loading = true;
      this.error = '';
      try {
        this.exportPayload = await exportTrip(this.trip.routing_solution, 'html');
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async exportCurrentFile() {
      if (!this.trip) {
        return;
      }
      this.loading = true;
      this.error = '';
      try {
        this.exportPayload = await exportTripFile(this.trip.routing_solution, 'html');
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async submitJob(payload: PlanTripRequest) {
      this.loading = true;
      this.error = '';
      try {
        const job = await submitPlanningJob(payload);
        this.activeJob = job;
        if (job.state) {
          this.trip = job.state;
        }
        await this.refreshJobs();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async refreshJobs() {
      try {
        this.jobs = await listPlanningJobs();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      }
    }
  }
});
