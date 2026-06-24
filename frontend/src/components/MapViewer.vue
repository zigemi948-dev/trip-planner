<script setup lang="ts">
import mapboxgl, { type GeoJSONSource, type LngLatBoundsLike, type Map, type Marker, type Style } from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { Coordinates, DayRoute, RouteStop } from '../types/trip';

const props = defineProps<{
  routes: DayRoute[];
}>();

const mapContainer = ref<HTMLDivElement | null>(null);
const mapError = ref('');
const mapReady = ref(false);
let map: Map | null = null;
let markers: Marker[] = [];

const routeColors = ['#1d4ed8', '#0f766e', '#b45309', '#7c3aed', '#be123c', '#0369a1'];
const routeCollection = computed<GeoJSON.FeatureCollection>(() => ({
  type: 'FeatureCollection',
  features: props.routes
    .filter((route) => routeCoordinates(route).length >= 2)
    .map((route, index) => ({
      type: 'Feature',
      properties: {
        day: route.day,
        color: routeColors[index % routeColors.length]
      },
      geometry: {
        type: 'LineString',
        coordinates: routeCoordinates(route).map(toLngLat)
      }
    }))
}));

const stopCollection = computed<GeoJSON.FeatureCollection>(() => ({
  type: 'FeatureCollection',
  features: props.routes.flatMap((route, routeIndex) =>
    route.stops.map((stop, stopIndex) => ({
      type: 'Feature',
      properties: {
        day: route.day,
        stopIndex: stopIndex + 1,
        title: stop.poi.name,
        subtitle: `${stop.arrival_time}-${stop.departure_time}`,
        color: routeColors[routeIndex % routeColors.length]
      },
      geometry: {
        type: 'Point',
        coordinates: toLngLat(stop.poi.coordinates)
      }
    }))
  )
}));

onMounted(async () => {
  await nextTick();
  initializeMap();
});

onBeforeUnmount(() => {
  clearMarkers();
  map?.remove();
  map = null;
});

watch(
  () => props.routes,
  () => {
    if (!map || !mapReady.value) {
      return;
    }
    renderRoutes();
  },
  { deep: true }
);

function initializeMap() {
  if (!mapContainer.value || map) {
    return;
  }

  const token = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;
  if (token) {
    mapboxgl.accessToken = token;
  }

  try {
    map = new mapboxgl.Map({
      container: mapContainer.value,
      style: token ? 'mapbox://styles/mapbox/streets-v12' : osmRasterStyle(),
      center: defaultCenter(),
      zoom: 12,
      attributionControl: true
    });
    map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-right');
    map.addControl(new mapboxgl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    map.on('load', () => {
      mapReady.value = true;
      addRouteLayers();
      renderRoutes();
    });
    map.on('error', (event) => {
      mapError.value = event.error?.message ?? 'Map failed to load.';
    });
  } catch (error) {
    mapError.value = error instanceof Error ? error.message : 'Map initialization failed.';
  }
}

function addRouteLayers() {
  if (!map) {
    return;
  }
  if (!map.getSource('trip-routes')) {
    map.addSource('trip-routes', {
      type: 'geojson',
      data: routeCollection.value
    });
  }
  if (!map.getSource('trip-stops')) {
    map.addSource('trip-stops', {
      type: 'geojson',
      data: stopCollection.value
    });
  }
  if (!map.getLayer('trip-route-lines')) {
    map.addLayer({
      id: 'trip-route-lines',
      type: 'line',
      source: 'trip-routes',
      layout: {
        'line-cap': 'round',
        'line-join': 'round'
      },
      paint: {
        'line-color': ['get', 'color'],
        'line-width': 5,
        'line-opacity': 0.9
      }
    });
  }
  if (!map.getLayer('trip-route-halo')) {
    map.addLayer(
      {
        id: 'trip-route-halo',
        type: 'line',
        source: 'trip-routes',
        layout: {
          'line-cap': 'round',
          'line-join': 'round'
        },
        paint: {
          'line-color': '#ffffff',
          'line-width': 9,
          'line-opacity': 0.72
        }
      },
      'trip-route-lines'
    );
  }
}

function renderRoutes() {
  if (!map) {
    return;
  }
  (map.getSource('trip-routes') as GeoJSONSource | undefined)?.setData(routeCollection.value);
  (map.getSource('trip-stops') as GeoJSONSource | undefined)?.setData(stopCollection.value);
  renderMarkers();
  fitToRoutes();
}

function renderMarkers() {
  if (!map) {
    return;
  }
  const currentMap = map;
  clearMarkers();
  props.routes.forEach((route, routeIndex) => {
    route.stops.forEach((stop, stopIndex) => {
      const markerNode = document.createElement('button');
      markerNode.type = 'button';
      markerNode.className = 'mapbox-stop-marker';
      markerNode.style.setProperty('--marker-color', routeColors[routeIndex % routeColors.length]);
      markerNode.textContent = String(stopIndex + 1);
      const popup = new mapboxgl.Popup({ offset: 20 }).setHTML(popupHtml(route, stop, stopIndex));
      const marker = new mapboxgl.Marker({ element: markerNode, anchor: 'center' })
        .setLngLat(toLngLat(stop.poi.coordinates))
        .setPopup(popup)
        .addTo(currentMap);
      markers.push(marker);
    });
  });
}

function fitToRoutes() {
  if (!map) {
    return;
  }
  const coordinates = allCoordinates();
  if (!coordinates.length) {
    map.easeTo({ center: defaultCenter(), zoom: 12, duration: 500 });
    return;
  }

  const bounds = new mapboxgl.LngLatBounds(toLngLat(coordinates[0]), toLngLat(coordinates[0]));
  coordinates.forEach((coordinate) => bounds.extend(toLngLat(coordinate)));
  map.fitBounds(bounds as LngLatBoundsLike, {
    padding: { top: 44, right: 44, bottom: 58, left: 44 },
    maxZoom: 15,
    duration: 700
  });
}

function clearMarkers() {
  markers.forEach((marker) => marker.remove());
  markers = [];
}

function routeCoordinates(route: DayRoute): Coordinates[] {
  if (route.geometry.length >= 2) {
    return route.geometry;
  }
  return route.stops.map((stop) => stop.poi.coordinates);
}

function allCoordinates(): Coordinates[] {
  return props.routes.flatMap((route) => [
    ...routeCoordinates(route),
    ...route.stops.map((stop) => stop.poi.coordinates)
  ]);
}

function defaultCenter(): [number, number] {
  const coordinates = allCoordinates();
  if (!coordinates.length) {
    return [121.4737, 31.2304];
  }
  const lng = coordinates.reduce((sum, coordinate) => sum + coordinate.lng, 0) / coordinates.length;
  const lat = coordinates.reduce((sum, coordinate) => sum + coordinate.lat, 0) / coordinates.length;
  return [lng, lat];
}

function toLngLat(coordinate: Coordinates): [number, number] {
  return [coordinate.lng, coordinate.lat];
}

function popupHtml(route: DayRoute, stop: RouteStop, stopIndex: number): string {
  return `
    <strong>D${route.day}.${stopIndex + 1} ${escapeHtml(stop.poi.name)}</strong>
    <span>${escapeHtml(stop.arrival_time)}-${escapeHtml(stop.departure_time)}</span>
    <span>${escapeHtml(stop.inbound_mode ?? 'Start')} 路 ¥${stop.inbound_cost.toFixed(2)}</span>
  `;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function osmRasterStyle(): Style {
  return {
    version: 8,
    sources: {
      osm: {
        type: 'raster' as const,
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors'
      }
    },
    layers: [
      {
        id: 'osm',
        type: 'raster' as const,
        source: 'osm'
      }
    ]
  };
}
</script>

<template>
  <section class="map-shell" aria-label="Route map">
    <div ref="mapContainer" class="mapbox-canvas" />
    <div v-if="!routes.length" class="map-empty">
      Build a trip to render the route on the map.
    </div>
    <div v-if="mapError" class="map-error">
      {{ mapError }}
    </div>
    <div class="map-legend">
      <span v-for="(route, index) in routes" :key="route.day">
        <i class="legend-color" :style="{ background: routeColors[index % routeColors.length] }"></i>
        Day {{ route.day }}
      </span>
    </div>
  </section>
</template>
