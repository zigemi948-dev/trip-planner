<script setup lang="ts">
import type { FitnessPoint, RouteQualityMetrics } from '../types/trip';

const props = defineProps<{
  metrics: RouteQualityMetrics;
  curve: FitnessPoint[];
}>();

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function modeEntries(): Array<[string, number]> {
  return Object.entries(props.metrics.mode_share);
}
</script>

<template>
  <section class="panel solver-monitor">
    <h2>Solver Monitor</h2>
    <div class="metric-grid">
      <div>
        <span>Stops</span>
        <strong>{{ metrics.total_stops }}</strong>
      </div>
      <div>
        <span>Distance</span>
        <strong>{{ metrics.total_distance_km.toFixed(2) }} km</strong>
      </div>
      <div>
        <span>Active Time</span>
        <strong>{{ metrics.total_minutes }} min</strong>
      </div>
      <div>
        <span>Budget Used</span>
        <strong>{{ percent(metrics.budget_usage_ratio) }}</strong>
      </div>
    </div>

    <div class="fitness-bars">
      <div v-for="point in curve" :key="point.epoch" class="fitness-row">
        <span>{{ point.label }}</span>
        <div class="bar-track">
          <i :style="{ width: `${Math.max(8, Math.min(100, point.score * 10))}%` }"></i>
        </div>
        <strong>{{ point.score.toFixed(2) }}</strong>
      </div>
    </div>

    <div class="mode-share">
      <span v-for="[mode, count] in modeEntries()" :key="mode">
        {{ mode }}: {{ count }}
      </span>
    </div>
  </section>
</template>
