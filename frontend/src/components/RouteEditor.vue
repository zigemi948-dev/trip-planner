<script setup lang="ts">
import type { DayRoute } from '../types/trip';

defineProps<{
  routes: DayRoute[];
}>();
</script>

<template>
  <!-- Route editing will later emit drag/drop interrupts to the backend graph. -->
  <section class="panel">
    <h2>Optimized Route</h2>
    <div v-if="routes.length === 0" class="empty">No route yet.</div>
    <article v-for="route in routes" :key="route.day" class="day-block">
      <header>
        <strong>Day {{ route.day }}</strong>
        <span>{{ route.total_minutes }} min · fitness {{ route.fitness_score }}</span>
      </header>
      <ol>
        <li v-for="stop in route.stops" :key="stop.poi.id">
          <time>{{ stop.arrival_time }}</time>
          <span>{{ stop.poi.name }}</span>
          <small>{{ stop.inbound_mode }} · ¥{{ stop.inbound_cost.toFixed(2) }}</small>
        </li>
      </ol>
    </article>
  </section>
</template>
