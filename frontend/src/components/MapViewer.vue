<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import html2canvas from 'html2canvas';
import type { Coordinates, DayRoute, POICandidate, RouteStop } from '../types/trip';

type AMapLngLat = [number, number];

type AMapOverlay = {
  setMap(map: AMapMap | null): void;
};

type AMapEvented = {
  on(eventName: string, handler: () => void): void;
};

type AMapMap = AMapEvented & {
  destroy(): void;
  addControl(control: unknown): void;
  setZoomAndCenter(zoom: number, center: AMapLngLat): void;
  setFitView(
    overlays: AMapOverlay[],
    immediately?: boolean,
    avoid?: [number, number, number, number],
    maxZoom?: number
  ): void;
};

type AMapMarker = AMapOverlay & AMapEvented;
type AMapPolyline = AMapOverlay;

type AMapInfoWindow = {
  close(): void;
  open(map: AMapMap, position: AMapLngLat): void;
  setContent(content: string): void;
};

type AMapNamespace = {
  Map: new (
    container: HTMLDivElement,
    options: {
      center: AMapLngLat;
      mapStyle?: string;
      resizeEnable?: boolean;
      viewMode?: '2D' | '3D';
      zoom: number;
      WebGLParams?: { preserveDrawingBuffer: boolean }; // 新增：声明 WebGL 参数类型
    }
  ) => AMapMap;
  Marker: new (options: {
    anchor?: string;
    content: HTMLElement;
    offset?: unknown;
    position: AMapLngLat;
    zIndex?: number;
  }) => AMapMarker;
  Pixel: new (x: number, y: number) => unknown;
  Polyline: new (options: {
    lineCap?: 'butt' | 'round' | 'square';
    lineJoin?: 'miter' | 'round' | 'bevel';
    path: AMapLngLat[];
    showDir?: boolean;
    strokeColor: string;
    strokeOpacity?: number;
    strokeWeight?: number;
    zIndex?: number;
  }) => AMapPolyline;
  InfoWindow: new (options: { autoMove?: boolean; offset?: unknown }) => AMapInfoWindow;
  Scale?: new () => unknown;
  ToolBar?: new (options?: Record<string, unknown>) => unknown;
  plugin(names: string[], callback: () => void): void;
};

declare global {
  interface Window {
    AMap?: AMapNamespace;
    _AMapSecurityConfig?: {
      securityJsCode: string;
    };
  }
}

const props = defineProps<{
  routes: DayRoute[];
  hotel?: POICandidate | null;
}>();

const mapContainer = ref<HTMLDivElement | null>(null);
const mapError = ref('');
const mapReady = ref(false);
let map: AMapMap | null = null;
let infoWindow: AMapInfoWindow | null = null;
let markers: AMapMarker[] = [];
let routeLines: AMapPolyline[] = [];
let amapLoadPromise: Promise<AMapNamespace> | null = null;

const routeColors = ['#1d4ed8', '#0f766e', '#b45309', '#7c3aed', '#be123c', '#0369a1'];

onMounted(async () => {
  await nextTick();
  void initializeMap();
});

onBeforeUnmount(() => {
  clearOverlays();
  infoWindow?.close();
  infoWindow = null;
  map?.destroy();
  map = null;
});

watch(
  () => [props.routes, props.hotel],
  () => {
    if (!map || !mapReady.value) {
      return;
    }
    renderRoutes();
  },
  { deep: true }
);

async function initializeMap() {
  if (!mapContainer.value || map) {
    return;
  }

  try {
    const amap = await loadAmap();
    if (!mapContainer.value || map) {
      return;
    }

    const currentMap = new amap.Map(mapContainer.value, {
      center: defaultCenter(),
      mapStyle: 'amap://styles/normal',
      resizeEnable: true,
      viewMode: '2D',
      zoom: 12,
      WebGLParams: { preserveDrawingBuffer: true } // 新增：保留绘制缓冲区以支持跨域截图
    });
    map = currentMap;
    infoWindow = new amap.InfoWindow({
      autoMove: true,
      offset: new amap.Pixel(0, -18)
    });

    amap.plugin(['AMap.ToolBar', 'AMap.Scale'], () => {
      if (!map || currentMap !== map) {
        return;
      }
      if (amap.ToolBar) {
        currentMap.addControl(new amap.ToolBar({ position: 'RT' }));
      }
      if (amap.Scale) {
        currentMap.addControl(new amap.Scale());
      }
    });

    currentMap.on('complete', () => {
      mapReady.value = true;
      renderRoutes();
    });
  } catch (error) {
    mapError.value = error instanceof Error ? error.message : 'Amap initialization failed.';
  }
}

function loadAmap(): Promise<AMapNamespace> {
  if (window.AMap) {
    return Promise.resolve(window.AMap);
  }
  if (amapLoadPromise) {
    return amapLoadPromise;
  }

  const key = (import.meta.env.VITE_AMAP_JS_KEY || import.meta.env.VITE_AMAP_API_KEY) as
    | string
    | undefined;
  if (!key) {
    return Promise.reject(
      new Error('Missing VITE_AMAP_JS_KEY. Set a Gaode Web JS API key before loading the route map.')
    );
  }

  const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE as string | undefined;
  if (securityCode) {
    window._AMapSecurityConfig = {
      securityJsCode: securityCode
    };
  }

  amapLoadPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(
      key
    )}&plugin=AMap.ToolBar,AMap.Scale`;
    script.onload = () => {
      if (window.AMap) {
        resolve(window.AMap);
        return;
      }
      reject(new Error('Amap JS API loaded without exposing window.AMap.'));
    };
    script.onerror = () => reject(new Error('Failed to load Amap JS API.'));
    document.head.appendChild(script);
  });

  return amapLoadPromise;
}

function renderRoutes() {
  if (!map || !window.AMap) {
    return;
  }
  clearOverlays();
  renderRouteLines(window.AMap);
  renderHotelMarker(window.AMap);
  renderMarkers(window.AMap);
  fitToRoutes();
}

function renderRouteLines(amap: AMapNamespace) {
  props.routes.forEach((route, routeIndex) => {
    const path = routeCoordinates(route).map(toLngLat);
    if (path.length < 2 || !map) {
      return;
    }
    const color = routeColors[routeIndex % routeColors.length];
    const halo = new amap.Polyline({
      lineCap: 'round',
      lineJoin: 'round',
      path,
      strokeColor: '#ffffff',
      strokeOpacity: 0.72,
      strokeWeight: 9,
      zIndex: 20
    });
    const line = new amap.Polyline({
      lineCap: 'round',
      lineJoin: 'round',
      path,
      showDir: true,
      strokeColor: color,
      strokeOpacity: 0.9,
      strokeWeight: 5,
      zIndex: 21
    });
    halo.setMap(map);
    line.setMap(map);
    routeLines.push(halo, line);
  });
}

function renderMarkers(amap: AMapNamespace) {
  if (!map) {
    return;
  }
  const currentMap = map;
  props.routes.forEach((route, routeIndex) => {
    route.stops.forEach((stop, stopIndex) => {
      const markerNode = document.createElement('button');
      markerNode.type = 'button';
      markerNode.className = 'amap-stop-marker';
      markerNode.style.setProperty('--marker-color', routeColors[routeIndex % routeColors.length]);
      markerNode.textContent = String(stopIndex + 1);
      markerNode.setAttribute('aria-label', `${route.day}.${stopIndex + 1} ${stop.poi.name}`);

      const position = toLngLat(stop.poi.coordinates);
      const marker = new amap.Marker({
        anchor: 'center',
        content: markerNode,
        offset: new amap.Pixel(0, 0),
        position,
        zIndex: 30
      });
      marker.on('click', () => {
        if (!infoWindow) {
          return;
        }
        infoWindow.setContent(popupHtml(route, stop, stopIndex));
        infoWindow.open(currentMap, position);
      });
      marker.setMap(currentMap);
      markers.push(marker);
    });
  });
}

function renderHotelMarker(amap: AMapNamespace) {
  if (!map || !props.hotel) {
    return;
  }
  const currentMap = map;
  const markerNode = document.createElement('button');
  markerNode.type = 'button';
  markerNode.className = 'amap-hotel-marker';
  markerNode.textContent = 'H';
  markerNode.setAttribute('aria-label', `Hotel ${props.hotel.name}`);

  const position = toLngLat(props.hotel.coordinates);
  const marker = new amap.Marker({
    anchor: 'center',
    content: markerNode,
    offset: new amap.Pixel(0, 0),
    position,
    zIndex: 40
  });
  marker.on('click', () => {
    if (!infoWindow || !props.hotel) {
      return;
    }
    infoWindow.setContent(hotelPopupHtml(props.hotel));
    infoWindow.open(currentMap, position);
  });
  marker.setMap(currentMap);
  markers.push(marker);
}

function fitToRoutes() {
  if (!map) {
    return;
  }
  const coordinates = allCoordinates();
  if (!coordinates.length) {
    map.setZoomAndCenter(12, defaultCenter());
    return;
  }

  const overlays = [...routeLines, ...markers];
  if (overlays.length) {
    map.setFitView(overlays, false, [44, 44, 58, 44], 15);
    return;
  }
  map.setZoomAndCenter(12, defaultCenter());
}

function clearOverlays() {
  routeLines.forEach((line) => line.setMap(null));
  markers.forEach((marker) => marker.setMap(null));
  routeLines = [];
  markers = [];
}

function routeCoordinates(route: DayRoute): Coordinates[] {
  if (route.geometry.length >= 2) {
    return route.geometry;
  }
  return route.stops.map((stop) => stop.poi.coordinates);
}

function allCoordinates(): Coordinates[] {
  const coordinates = props.routes.flatMap((route) => [
    ...routeCoordinates(route),
    ...route.stops.map((stop) => stop.poi.coordinates)
  ]);
  if (props.hotel) {
    coordinates.push(props.hotel.coordinates);
  }
  return coordinates;
}

function defaultCenter(): AMapLngLat {
  const coordinates = allCoordinates();
  if (!coordinates.length) {
    return [121.4737, 31.2304];
  }
  const lng = coordinates.reduce((sum, coordinate) => sum + coordinate.lng, 0) / coordinates.length;
  const lat = coordinates.reduce((sum, coordinate) => sum + coordinate.lat, 0) / coordinates.length;
  return [lng, lat];
}

function toLngLat(coordinate: Coordinates): AMapLngLat {
  return [coordinate.lng, coordinate.lat];
}

function popupHtml(route: DayRoute, stop: RouteStop, stopIndex: number): string {
  return `
    <div class="amap-popup-content">
      <strong>D${route.day}.${stopIndex + 1} ${escapeHtml(stop.poi.name)}</strong>
      <span>${escapeHtml(stop.arrival_time)}-${escapeHtml(stop.departure_time)}</span>
      <span>${escapeHtml(stop.inbound_mode ?? 'Start')} 交通 ¥${stop.inbound_cost.toFixed(2)}</span>
    </div>
  `;
}

function hotelPopupHtml(hotel: POICandidate): string {
  return `
    <div class="amap-popup-content">
      <strong>入住酒店 ${escapeHtml(hotel.name)}</strong>
      <span>${escapeHtml(hotel.category)}</span>
      <span>${hotel.coordinates.lng.toFixed(5)}, ${hotel.coordinates.lat.toFixed(5)}</span>
    </div>
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

// 新增：地图快照提取核心逻辑
async function exportMapSnapshot(): Promise<string | null> {
  if (!mapContainer.value) {
    return null;
  }
  try {
    // 调用 html2canvas 对 DOM 树及内部包含的 WebGL Canvas 进行重绘合成
    const canvas = await html2canvas(mapContainer.value, {
      useCORS: true, // 允许加载跨域的地图瓦片资源
      allowTaint: false,
      backgroundColor: null // 保持原有背景
    });
    return canvas.toDataURL('image/png');
  } catch (error) {
    console.error('地图快照生成失败:', error);
    return null;
  }
}

// 新增：向父组件暴露接口
defineExpose({
  exportMapSnapshot
});

</script>

<template>
  <section class="map-shell" aria-label="Route map">
    <div ref="mapContainer" class="amap-canvas" />
    <div v-if="!routes.length" class="map-empty">
      Build a trip to render the route on the map.
    </div>
    <div v-if="mapError" class="map-error">
      {{ mapError }}
    </div>
    <div class="map-legend">
      <span v-if="hotel">
        <i class="legend-color hotel-legend"></i>
        Hotel
      </span>
      <span v-for="(route, index) in routes" :key="route.day">
        <i class="legend-color" :style="{ background: routeColors[index % routeColors.length] }"></i>
        Day {{ route.day }}
      </span>
    </div>
  </section>
</template>
