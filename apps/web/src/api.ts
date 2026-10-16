// Typed client for the FastAPI backend (same origin: Vite proxy in dev, FastAPI in prod).

export type CameraStatus = "online" | "offline" | "connecting" | "unknown";

export interface Camera {
  id: number;
  name: string;
  location: string | null;
  stream_url_masked: string;
  enabled: boolean;
  status: CameraStatus;
  last_seen_at: string | null;
  infer_fps: number | null;
  created_at: string;
}

export interface CameraInput {
  name: string;
  stream_url?: string;
  location?: string | null;
  enabled?: boolean;
}

export interface CameraTestResult {
  ok: boolean;
  width: number | null;
  height: number | null;
  codec: string | null;
  error: string | null;
}

export type EventStatus = "unreviewed" | "acknowledged" | "resolved" | "false_alarm";
export type Severity = "high" | "medium" | "low";

export interface VisionEvent {
  id: number;
  camera_id: number;
  camera_name: string | null;
  event_type: string;
  severity: Severity;
  occurred_at: string;
  rule_id: string | null;
  rule_name: string | null;
  object_class: string | null;
  track_id: number | null;
  confidence: number | null;
  details: Record<string, unknown>;
  snapshot_url: string | null;
  status: EventStatus;
  note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface EventPage {
  total: number;
  items: VisionEvent[];
}

export interface EventFilters {
  camera_id?: string;
  event_type?: string;
  status?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}

export interface Summary {
  window_hours: number;
  cameras: { total: number; by_status: Partial<Record<CameraStatus, number>> };
  events: { total: number; by_type: Record<string, number> };
  unreviewed: number;
}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  cameras: () => request<Camera[]>("/api/cameras"),
  createCamera: (body: CameraInput) => request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(body) }),
  updateCamera: (id: number, body: Partial<CameraInput>) =>
    request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCamera: (id: number) => request<void>(`/api/cameras/${id}`, { method: "DELETE" }),
  testCamera: (id: number) => request<CameraTestResult>(`/api/cameras/${id}/test`, { method: "POST" }),

  events: (f: EventFilters) => request<EventPage>(`/api/events${query({ ...f })}`),
  event: (id: number | string) => request<VisionEvent>(`/api/events/${id}`),
  updateEvent: (id: number, body: { status?: EventStatus; note?: string; reviewed_by?: string }) =>
    request<VisionEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  summary: (hours = 24) => request<Summary>(`/api/analytics/summary${query({ hours })}`),
};
