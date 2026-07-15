import type {
  HealthResponse,
  IntegrationProbeResponse,
  IntentConstraints,
  PlanningJob,
  PlanningJobEvents,
  PlanningJobSummary,
  ReplanRequest,
  RoutingSolution,
  RuntimeCapabilities,
  SavedTrip,
  SavedTripReplanResult,
  TripState,
  WorkflowEvent
} from '../types/trip';

const API_BASE = '/api';

export interface PlanTripRequest {
  // This mirrors backend IntentConstraints so the UI submits normalized intent.
  user_query: string;
  destination: string;
  days: number;
  time_window_baseline?: [string, string];
  budget_limit: number;
  preferences: string[];
}

export type ExportFormat = 'html' | 'pdf' | 'png';

export async function parseIntent(userQuery: string): Promise<IntentConstraints> {
  const response = await fetch(`${API_BASE}/trips/intent/parse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_query: userQuery })
  });

  if (!response.ok) {
    throw new Error(`Intent parse failed: ${response.status}`);
  }

  return response.json();
}

export async function planTrip(payload: PlanTripRequest): Promise<TripState> {
  // Vite proxies /api to FastAPI during local development.
  const response = await fetch(`${API_BASE}/trips/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`Plan request failed: ${response.status}`);
  }

  return response.json();
}

export async function loadDemoTrip(): Promise<TripState> {
  // The demo endpoint keeps frontend work unblocked before real MCP APIs exist.
  const response = await fetch(`${API_BASE}/trips/demo`);
  if (!response.ok) {
    throw new Error(`Demo request failed: ${response.status}`);
  }
  return response.json();
}

export async function replanTrip(payload: ReplanRequest): Promise<TripState> {
  const response = await fetch(`${API_BASE}/trips/replan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`Replan request failed: ${response.status}`);
  }

  return response.json();
}

// 修改 exportTrip 方法
export async function exportTrip(
  solution: RoutingSolution, 
  exportFormat: ExportFormat = 'html',
  mapSnapshotBase64?: string | null // 新增参数
): Promise<Record<string, string>> {
  const response = await fetch(`${API_BASE}/trips/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ 
      solution, 
      export_format: exportFormat,
      map_snapshot_base64: mapSnapshotBase64 // 注入 Payload
    })
  });

  if (!response.ok) {
    throw new Error(`Export request failed: ${response.status}`);
  }

  return response.json();
}

// 修改 exportTripFile 方法
export async function exportTripFile(
  solution: RoutingSolution, 
  exportFormat: ExportFormat = 'html',
  mapSnapshotBase64?: string | null // 新增参数
): Promise<Record<string, string>> {
  const response = await fetch(`${API_BASE}/trips/export/file`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ 
      solution, 
      export_format: exportFormat,
      map_snapshot_base64: mapSnapshotBase64 // 注入 Payload
    })
  });

  if (!response.ok) {
    throw new Error(`Export file request failed: ${response.status}`);
  }

  return response.json();
}

export async function saveTrip(state: TripState): Promise<SavedTrip> {
  const response = await fetch(`${API_BASE}/trips/saved`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state)
  });
  if (!response.ok) {
    throw new Error(`Save trip request failed: ${response.status}`);
  }
  return response.json();
}

export async function replanSavedTrip(
  tripId: string,
  day: number,
  newPoi: ReplanRequest['new_poi']
): Promise<SavedTripReplanResult> {
  const response = await fetch(`${API_BASE}/trips/saved/${tripId}/replan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ day, new_poi: newPoi })
  });
  if (!response.ok) {
    throw new Error(`Saved replan request failed: ${response.status}`);
  }
  return response.json();
}

export async function exportSavedTripFile(
  tripId: string,
  exportFormat: ExportFormat = 'html',
  mapSnapshotBase64?: string | null
): Promise<Record<string, string>> {
  const response = await fetch(`${API_BASE}/trips/saved/${tripId}/export/file`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      export_format: exportFormat,
      map_snapshot_base64: mapSnapshotBase64
    })
  });
  if (!response.ok) {
    throw new Error(`Saved export file request failed: ${response.status}`);
  }
  return response.json();
}

export async function submitPlanningJob(payload: PlanTripRequest): Promise<PlanningJob> {
  const response = await fetch(`${API_BASE}/trips/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`Job request failed: ${response.status}`);
  }

  return response.json();
}

export async function listPlanningJobs(): Promise<PlanningJobSummary[]> {
  const response = await fetch(`${API_BASE}/trips/jobs`);
  if (!response.ok) {
    throw new Error(`Job list request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchPlanningJobEvents(jobId: string, after = 0): Promise<PlanningJobEvents> {
  const response = await fetch(`${API_BASE}/trips/jobs/${jobId}/events?after=${after}`);
  if (!response.ok) {
    throw new Error(`Job events request failed: ${response.status}`);
  }
  return response.json();
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await fetch('/health');
  if (!response.ok) {
    throw new Error(`Health request failed: ${response.status}`);
  }
  return response.json();
}

export async function loadRuntimeCapabilities(): Promise<RuntimeCapabilities> {
  const response = await fetch('/health/capabilities');
  if (!response.ok) {
    throw new Error(`Capabilities request failed: ${response.status}`);
  }
  return response.json();
}

export async function probeIntegrations(): Promise<IntegrationProbeResponse> {
  const response = await fetch('/health/integrations/probe');
  if (!response.ok) {
    throw new Error(`Integration probe failed: ${response.status}`);
  }
  return response.json();
}

export function streamTripPlan(
  payload: PlanTripRequest,
  onEvent: (event: WorkflowEvent) => void,
): Promise<TripState> {
  return new Promise((resolve, reject) => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws/solve`);

    socket.addEventListener('open', () => {
      socket.send(JSON.stringify(payload));
    });

    socket.addEventListener('message', (message) => {
      const event = JSON.parse(message.data) as WorkflowEvent;
      onEvent(event);
      if (event.event === 'complete') {
        resolve(event.payload as unknown as TripState);
      }
      if (event.event === 'failed') {
        reject(new Error(String(event.payload.reason ?? 'Stream solve failed')));
      }
    });

    socket.addEventListener('error', () => {
      reject(new Error('WebSocket connection failed'));
    });
  });
}
