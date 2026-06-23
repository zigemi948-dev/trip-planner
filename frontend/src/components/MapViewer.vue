<script setup lang="ts">
import type { Coordinates, DayRoute } from '../types/trip';

const props = defineProps<{
  routes: DayRoute[];
}>();

const width = 960;
const height = 360;
const padding = 34;
const verticalGridLines = Array.from({ length: 7 }, (_, index) => (index + 1) * 120);
const horizontalGridLines = Array.from({ length: 3 }, (_, index) => (index + 1) * 90);

interface MapBounds {
  minLat: number;
  maxLat: number;
  minLng: number;
  maxLng: number;
}

interface ProjectedPoint {
  x: number;
  y: number;
}

type RouteStop = DayRoute['stops'][number];

function getAllPoints(): Coordinates[] {
  return props.routes.flatMap((route: DayRoute) => [
    ...route.geometry,
    ...route.stops.map((stop: RouteStop) => stop.poi.coordinates)
  ]);
}

function getBounds(): MapBounds | null {
  const points = getAllPoints();
  if (points.length === 0) {
    return null;
  }

  return {
    minLat: Math.min(...points.map((point: Coordinates) => point.lat)),
    maxLat: Math.max(...points.map((point: Coordinates) => point.lat)),
    minLng: Math.min(...points.map((point: Coordinates) => point.lng)),
    maxLng: Math.max(...points.map((point: Coordinates) => point.lng))
  };
}

function project(point: Coordinates): ProjectedPoint {
  const box = getBounds();
  if (!box) {
    return { x: width / 2, y: height / 2 };
  }

  const lngSpan = Math.max(box.maxLng - box.minLng, 0.0001);
  const latSpan = Math.max(box.maxLat - box.minLat, 0.0001);
  const x = padding + ((point.lng - box.minLng) / lngSpan) * (width - padding * 2);
  const y = height - padding - ((point.lat - box.minLat) / latSpan) * (height - padding * 2);
  return { x, y };
}

function routePath(route: DayRoute): string {
  const points = route.geometry.length
    ? route.geometry
    : route.stops.map((stop: RouteStop) => stop.poi.coordinates);
  return points
    .map((point: Coordinates, index: number) => {
      const projected = project(point);
      return `${index === 0 ? 'M' : 'L'} ${projected.x.toFixed(1)} ${projected.y.toFixed(1)}`;
    })
    .join(' ');
}

function stopPosition(stop: RouteStop): ProjectedPoint {
  return project(stop.poi.coordinates);
}

function stopLabel(index: number | string): number {
  return Number(index) + 1;
}
</script>

<template>
  <section class="map-shell" aria-label="Route map">
    <svg
      class="route-map"
      :viewBox="`0 0 ${width} ${height}`"
      role="img"
      aria-label="Projected route geometry"
      >
      <g class="grid-lines">
        <line v-for="x in verticalGridLines" :key="`x-${x}`" :x1="x" y1="0" :x2="x" :y2="height" />
        <line v-for="y in horizontalGridLines" :key="`y-${y}`" x1="0" :y1="y" :x2="width" :y2="y" />
      </g>
      <g v-for="route in routes" :key="route.day" class="route-layer">
        <path
          class="route-path"
          :class="`route-path-${route.day}`"
          :d="routePath(route)"
        />
        <g
          v-for="(stop, index) in route.stops"
          :key="stop.poi.id"
          class="stop-marker"
          :transform="`translate(${stopPosition(stop).x}, ${stopPosition(stop).y})`"
        >
          <circle r="12" />
          <text y="4">{{ stopLabel(index) }}</text>
          <title>D{{ route.day }} {{ stop.arrival_time }} {{ stop.poi.name }}</title>
        </g>
      </g>
    </svg>
    <div class="map-legend">
      <span v-for="route in routes" :key="route.day">
        <i :class="`legend-color route-color-${route.day}`"></i>
        Day {{ route.day }}
      </span>
    </div>
  </section>
</template>
