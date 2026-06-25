<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue';
import BudgetDashboard from '../components/BudgetDashboard.vue';
import MapViewer from '../components/MapViewer.vue';
import RouteEditor from '../components/RouteEditor.vue';
import SolverMonitor from '../components/SolverMonitor.vue';
import { useTripStore } from '../stores/tripStore';
import type { POICandidate } from '../types/trip';

const store = useTripStore();
// Default values are aligned with the backend demo endpoint for quick testing.
const form = reactive({
  user_query: 'Plan a balanced two-day city trip under budget.',
  destination: 'Shanghai',
  days: 2,
  time_window_baseline: ['09:00', '19:00'] as [string, string],
  budget_limit: 600,
  preferences: ['museum', 'food', 'landmark']
});

const solution = computed(() => store.trip?.routing_solution);
const preferencesText = computed({
  get: () => form.preferences.join(', '),
  set: (value: string) => {
    form.preferences = value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  }
});
const events = computed(() => {
  const streamEvents = store.streamEvents.slice(-8);
  return streamEvents.length ? streamEvents : store.trip?.graph_controls.events.slice(-8) ?? [];
});
const hotelStayByDay = computed(() => {
  const entries = solution.value?.hotel_stays ?? [];
  return new Map(entries.map((stay) => [stay.day, stay]));
});

const libraryPoi: POICandidate = {
  id: 'poi_library',
  name: 'City Library',
  category: 'library',
  coordinates: { lat: 31.226, lng: 121.471 },
  fixed_cost: 0,
  visit_duration_minutes: 60,
  utility: 6.4,
  opening_window: ['09:00', '20:00'],
  indoor: true
};

onMounted(() => {
  store.checkBackend();
  store.refreshJobs();
});

async function parseRequest() {
  const parsed = await store.parseRequest(form.user_query);
  if (!parsed) {
    return;
  }
  form.destination = parsed.destination;
  form.days = parsed.days;
  form.time_window_baseline = parsed.time_window_baseline ?? ['09:00', '19:00'];
  form.budget_limit = parsed.budget_limit;
  form.preferences = parsed.preferences;
}

function temperatureLabel(min: number | null, max: number | null): string {
  if (min !== null && max !== null) {
    return `${Math.round(min)}-${Math.round(max)} °C`;
  }
  if (max !== null) {
    return `${Math.round(max)} °C`;
  }
  if (min !== null) {
    return `${Math.round(min)} °C`;
  }
  return 'Temp unavailable';
}
</script>

<template>
  <main class="workspace">
    <aside class="sidebar">
      <h1>Trip Planner</h1>
      <label>
        Destination
        <input v-model="form.destination" />
      </label>
      <label>
        Days
        <input v-model.number="form.days" type="number" min="1" max="14" />
      </label>
      <label>
        Budget
        <input v-model.number="form.budget_limit" type="number" min="1" />
      </label>
      <label>
        Preferences
        <input v-model="preferencesText" />
      </label>
      <label>
        Request
        <textarea v-model="form.user_query" rows="4" />
      </label>
      <button :disabled="store.loading" class="secondary" @click="parseRequest">
        Parse Request
      </button>
      <button :disabled="store.loading" @click="store.plan(form)">
        {{ store.loading ? 'Solving...' : 'Plan Trip' }}
      </button>
      <button :disabled="store.loading" class="secondary" @click="store.demo()">
        Load Demo
      </button>
      <button :disabled="store.loading" class="secondary" @click="store.streamPlan(form)">
        {{ store.streaming ? 'Streaming...' : 'Stream Solve' }}
      </button>
      <button :disabled="store.loading" class="secondary" @click="store.submitJob(form)">
        Submit Job
      </button>
      <button :disabled="store.loading || !store.trip" class="secondary" @click="store.insertPoi(1, libraryPoi)">
        Insert Library
      </button>
      <button :disabled="store.loading || !solution" class="secondary" @click="store.exportCurrent()">
        Export HTML
      </button>
      <button :disabled="store.loading || !solution" class="secondary" @click="store.exportCurrentFile()">
        Export File
      </button>
      <p v-if="store.error" class="error">{{ store.error }}</p>
      <p v-if="store.exportPayload" class="export-note">
        Export ready: {{ store.exportPayload.file_path ?? store.exportPayload.format }}
      </p>
      <section class="job-card">
        <strong>Backend</strong>
        <span>{{ store.health ? `${store.health.status} · ${store.health.version}` : 'offline' }}</span>
      </section>
      <section v-if="store.capabilities" class="job-card capability-card">
        <header>
          <strong>Runtime</strong>
          <span class="inline-actions">
            <button class="mini-button" type="button" @click="store.refreshCapabilities()">Refresh</button>
            <button class="mini-button" type="button" :disabled="store.probingIntegrations" @click="store.probeRuntimeIntegrations()">
              {{ store.probingIntegrations ? 'Probing' : 'Probe' }}
            </button>
          </span>
        </header>
        <span>Provider: {{ store.capabilities.provider_mode }}</span>
        <span :class="{ warning: store.capabilities.fallback_mode }">
          Amap: {{ store.capabilities.amap_enabled ? 'enabled' : store.capabilities.amap_configured ? 'configured' : 'fallback' }}
        </span>
        <span>
          LLM: {{ store.capabilities.llm_enabled ? store.capabilities.llm_model : store.capabilities.llm_configured ? 'configured' : 'off' }}
        </span>
        <div v-if="store.integrationProbe" class="probe-results">
          <span
            v-for="result in store.integrationProbe.results"
            :key="result.name"
            :class="`probe-${result.status}`"
          >
            {{ result.name }}: {{ result.status }} · {{ result.message }}
          </span>
        </div>
      </section>
      <section v-if="store.activeJob" class="job-card">
        <strong>Job {{ store.activeJob.id.slice(0, 8) }}</strong>
        <span>{{ store.activeJob.status }}</span>
      </section>
    </aside>

    <section class="main-stage">
      <MapViewer :routes="solution?.optimized_route ?? []" :hotel="solution?.hotel_anchor ?? null" />
      <div class="lower-grid">
        <RouteEditor :routes="solution?.optimized_route ?? []" />
        <BudgetDashboard
          v-if="solution"
          :budget="solution.budget_breakdown"
        />
      </div>
      <section v-if="solution" class="panel daily-details">
        <h2>Daily Conditions</h2>
        <article v-for="forecast in solution.daily_weather" :key="forecast.day">
          <strong>
            Day {{ forecast.day }} {{ forecast.date ? `- ${forecast.date}` : '' }}
          </strong>
          <span>
            Weather: {{ forecast.weather || 'unknown' }} - {{ temperatureLabel(forecast.temperature_min, forecast.temperature_max) }}
            <template v-if="forecast.wind"> - Wind {{ forecast.wind }}</template>
          </span>
          <span v-if="forecast.advisory">{{ forecast.advisory }}</span>
          <span v-if="hotelStayByDay.get(forecast.day)">
            Hotel: {{ hotelStayByDay.get(forecast.day)?.hotel.name }}
            - check-in {{ hotelStayByDay.get(forecast.day)?.check_in_time }}
            - depart {{ hotelStayByDay.get(forecast.day)?.check_out_time }}
          </span>
        </article>
      </section>
      <SolverMonitor
        v-if="solution"
        :metrics="solution.quality_metrics"
        :curve="solution.fitness_curve"
      />
      <section v-if="solution" class="panel narrative">
        <h2>Narrative</h2>
        <p>{{ solution.narrative }}</p>
        <p v-for="warning in solution.warnings" :key="warning" class="warning">
          {{ warning }}
        </p>
      </section>
      <section class="panel events">
        <h2>Workflow Events</h2>
        <ul>
          <li v-for="event in events" :key="`${event.event}-${JSON.stringify(event.payload)}`">
            <span>{{ event.event }}</span>
            <small>{{ event.payload }}</small>
          </li>
        </ul>
      </section>
      <section class="panel events">
        <h2>Stored Jobs</h2>
        <ul>
          <li v-for="job in store.jobs" :key="job.id">
            <span>{{ job.id.slice(0, 8) }}</span>
            <small>{{ job.destination }} · {{ job.days }} day(s) · {{ job.status }}</small>
          </li>
        </ul>
      </section>
    </section>
  </main>
</template>
