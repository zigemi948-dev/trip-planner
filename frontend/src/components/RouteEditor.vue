<script setup lang="ts">
import type { DayRoute, RouteStop } from '../types/trip';

defineProps<{
  routes: DayRoute[];
}>();

function transitText(stop: RouteStop): string {
  if (stop.inbound_mode !== 'Transit') {
    return '';
  }
  const boarding = stop.inbound_boarding_station || 'origin nearby transit stop';
  const alighting = stop.inbound_alighting_station || 'destination nearby transit stop';
  return `Board: ${boarding} / Alight: ${alighting}`;
}
</script>

<template>
  <section class="panel">
    <h2>Optimized Route</h2>
    <div v-if="routes.length === 0" class="empty">No route yet.</div>
    <article v-for="route in routes" :key="route.day" class="day-block">
      <header>
        <strong>Day {{ route.day }}</strong>
        <span>{{ route.total_minutes }} min - fitness {{ route.fitness_score }}</span>
      </header>
      <div v-if="route.cost_breakdown" class="day-costs">
        <span>Hotel ¥{{ route.cost_breakdown.accommodation_cost.toFixed(2) }}</span>
        <span>Tickets ¥{{ route.cost_breakdown.ticket_cost.toFixed(2) }}</span>
        <span>Food ¥{{ route.cost_breakdown.food_cost.toFixed(2) }}</span>
        <span>Transport ¥{{ route.cost_breakdown.transport_cost.toFixed(2) }}</span>
        <strong>Total ¥{{ route.cost_breakdown.total_cost.toFixed(2) }}</strong>
      </div>
      <ol>
        <li v-for="stop in route.stops" :key="stop.poi.id">
          <time>{{ stop.arrival_time }}</time>
          <span>
            {{ stop.poi.name }}
            <small v-if="transitText(stop)" class="transit-stations">{{ transitText(stop) }}</small>
          </span>
          <small>{{ stop.inbound_mode }} - ¥{{ stop.inbound_cost.toFixed(2) }}</small>
        </li>
      </ol>
    </article>
  </section>
</template>
