import { defineStore } from 'pinia';
import {
  checkHealth,
  exportTrip,
  exportSavedTripFile,
  fetchPlanningJobEvents,
  listPlanningJobs,
  loadRuntimeCapabilities,
  loadDemoTrip,
  parseIntent,
  planTrip,
  probeIntegrations,
  replanTrip,
  replanSavedTrip,
  saveTrip,
  streamTripPlan,
  submitPlanningJob,
  type PlanTripRequest,
  type ExportFormat 
} from '../api/trips';
import type {
  HealthResponse,
  IntegrationProbeResponse,
  PlanningJob,
  PlanningJobEvents,
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
    savedTripId: null as string | null,
    jobs: [] as PlanningJobSummary[],
    health: null as HealthResponse | null,
    capabilities: null as RuntimeCapabilities | null,
    integrationProbe: null as IntegrationProbeResponse | null,
    probingIntegrations: false,
    parsedIntent: null as PlanTripRequest | null,
    streamEvents: [] as WorkflowEvent[],
    streaming: false,
    jobEventOffset: 0,
    jobPollingTimer: null as number | null
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
        this.savedTripId = null;
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
        this.savedTripId = null;
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
        this.savedTripId = null;
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
        if (this.savedTripId) {
          const result = await replanSavedTrip(this.savedTripId, day, newPoi);
          this.trip = result.state;
        } else {
          this.trip = await replanTrip({
            state: this.trip,
            day,
            new_poi: newPoi
          });
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },

    async exportCurrent(format: ExportFormat = 'html', mapSnapshot?: string | null) {
      if (!this.trip) {
        return;
      }
      this.loading = true;
      this.error = '';
      try {
        this.exportPayload = await exportTrip(this.trip.routing_solution, format, mapSnapshot);
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },

    async exportCurrentFile(format: ExportFormat = 'html', mapSnapshot?: string | null) {
      if (!this.trip) {
        return;
      }
      this.loading = true;
      this.error = '';
      try {
        if (!this.savedTripId) {
          const saved = await saveTrip(this.trip);
          this.savedTripId = saved.id;
        }
        this.exportPayload = await exportSavedTripFile(this.savedTripId, format, mapSnapshot);
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    async submitJob(payload: PlanTripRequest) {
      this.stopJobPolling();
      this.loading = true;
      this.error = '';
      this.streamEvents = [];
      this.savedTripId = null;
      try {
        const job = await submitPlanningJob(payload);
        this.activeJob = job;
        this.jobEventOffset = 0;
        await this.refreshJobs();
        await this.pollJob(job.id);
        if (!['complete', 'failed'].includes(this.activeJob?.status ?? '')) {
          this.startJobPolling(job.id);
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
      } finally {
        this.loading = false;
      }
    },
    startJobPolling(jobId: string) {
      this.stopJobPolling();
      this.jobPollingTimer = window.setInterval(() => {
        void this.pollJob(jobId);
      }, 1000);
    },
    stopJobPolling() {
      if (this.jobPollingTimer !== null) {
        window.clearInterval(this.jobPollingTimer);
        this.jobPollingTimer = null;
      }
    },
    async pollJob(jobId: string) {
      try {
        const eventWindow = await fetchPlanningJobEvents(jobId, this.jobEventOffset);
        this.applyJobEventWindow(eventWindow);
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Unknown error';
        this.stopJobPolling();
      }
    },
    applyJobEventWindow(eventWindow: PlanningJobEvents) {
      this.jobEventOffset = eventWindow.next_offset;
      if (eventWindow.events.length) {
        this.streamEvents.push(...eventWindow.events);
      }
      if (this.activeJob?.id === eventWindow.job_id) {
        this.activeJob = {
          ...this.activeJob,
          status: eventWindow.status,
          events: [...this.activeJob.events, ...eventWindow.events],
          state: eventWindow.state ?? this.activeJob.state,
          saved_trip_id: eventWindow.saved_trip_id ?? this.activeJob.saved_trip_id
        };
      }
      if (eventWindow.saved_trip_id) {
        this.savedTripId = eventWindow.saved_trip_id;
      }
      if (eventWindow.state) {
        this.trip = eventWindow.state;
      }
      if (['complete', 'failed'].includes(eventWindow.status)) {
        this.stopJobPolling();
        void this.refreshJobs();
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
