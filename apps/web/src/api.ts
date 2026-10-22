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
  notify_muted_until: string | null; // phone notifications silenced until then
}

export interface CameraInput {
  name: string;
  stream_url?: string;
  location?: string | null;
  enabled?: boolean;
}

export interface FoundDevice {
  ip: string;
  name: string | null;
  manufacturer: string | null;
  model: string | null;
  onvif_url: string | null;
  rtsp_ports: number[];
  brand: string | null;
  suggestions: { label: string; url: string }[];
  already_added: boolean;
}

export interface Discovery {
  subnets: string[];
  devices: FoundDevice[];
}

export interface OnvifStream {
  name: string;
  width: number;
  height: number;
  url: string;
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
  clip_url: string | null;
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
  since?: string;
  limit?: number;
  offset?: number;
}

export interface Summary {
  window_hours: number;
  cameras: { total: number; by_status: Partial<Record<CameraStatus, number>> };
  events: { total: number; by_type: Record<string, number> };
  unreviewed: number;
}

export type Role = "admin" | "operator";

export interface User {
  id: number;
  username: string;
  role: Role;
  active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AuthStatus {
  setup_required: boolean;
  user: User | null;
}

export type RuleType = "zone_entry" | "line_crossing" | "loitering" | "occupancy" | "privacy_mask";
export const isLine = (t: RuleType) => t === "line_crossing";
export type Point = [number, number];

export interface Rule {
  id: number;
  camera_id: number;
  type: RuleType;
  name: string;
  points: Point[];
  classes: string[];
  severity: Severity;
  cooldown_seconds: number;
  direction: "in" | "out" | "both";
  alert: boolean; // lines: false = count crossings only
  dwell_seconds: number;
  max_count: number;
  min_confidence: number;
  schedule: Schedule | null;
  enabled: boolean;
}

/** Active hours in the edge box's local time; days 0 = Monday. start == end means all day. */
export interface Schedule {
  days: number[];
  start: string;
  end: string;
}

export interface Sighting {
  id: number;
  camera_id: number;
  camera_name: string | null;
  object_class: string;
  first_seen: string;
  last_seen: string;
  duration_seconds: number;
  confidence: number;
  thumbnail_url: string;
  context_url: string;
  score: number | null;
}

export interface SightingDetail extends Sighting {
  path: [number, number][];
  clips: { event_id: number; event_type: string; occurred_at: string; clip_url: string | null; snapshot_url: string | null }[];
}

export interface Arming {
  armed: boolean;
  changed_by: string | null;
  changed_at: string | null;
}

export interface RulePerformance {
  camera_id: number;
  camera_name: string;
  rule_id: string;
  rule_name: string | null;
  event_type: string;
  total: number;
  unreviewed: number;
  acknowledged: number;
  resolved: number;
  false_alarm: number;
  false_alarm_rate: number | null;
}

export type RuleInput = Omit<Rule, "id" | "camera_id">;

export interface EntryLine {
  camera_id: number;
  camera_name: string;
  rule_id: number | null; // null once the counting line was deleted
  rule_name: string;
  in_total: number;
  out_total: number;
  by_class: Record<string, { in: number; out: number }>;
  in: number[];
  out: number[];
}

export interface Entries {
  bucket: "hour" | "day";
  buckets: string[];
  total: { in: number; out: number };
  lines: EntryLine[];
}

export interface Timeseries {
  bucket: "hour" | "day";
  buckets: string[];
  counts: Record<string, { peak: number[]; avg: number[] }>;
  events: Record<string, number[]>;
}

export interface AppSettings {
  event_retention_days: number;
  count_retention_days: number;
  clips_enabled: boolean;
  clip_retention_days: number;
  clip_storage_mb: number;
  sighting_retention_days: number;
  search_enabled: boolean;
  analysis_fps: number;
  detection_sensitivity: "high" | "balanced" | "low";
  motion_skip: boolean;
  tamper_alerts: boolean;
}

export interface NotificationSettings {
  enabled: boolean;
  server: string;
  topic: string;
  min_severity: Severity;
  include_snapshot: boolean;
  dashboard_url: string;
  cooldown_seconds: number;
  daily_summary: boolean;
  summary_time: string;
  has_token: boolean;
}

export type NotificationUpdate = Partial<Omit<NotificationSettings, "has_token">> & { token?: string };

export interface SystemHealth {
  database: "ok" | "error";
  worker: { last_contact: string | null; connected: boolean };
  storage: { snapshots: number; snapshot_bytes: number; disk_free_bytes: number; disk_total_bytes: number };
  events_total: number;
  problems: { key: string; title: string; message: string }[];
}

export interface AuditEntry {
  id: number;
  at: string;
  username: string | null;
  action: string;
  target: string | null;
  detail: Record<string, unknown>;
  ip: string | null;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Fired when the session has expired, so the app can show the login screen. */
export const UNAUTHORIZED_EVENT = "aiv:unauthorized";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    // The custom header is the API's CSRF check: browsers can't send it cross-site.
    headers: { "Content-Type": "application/json", "X-Requested-With": "aivision", ...init?.headers },
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status);
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
  discoverCameras: () => request<Discovery>("/api/cameras/discover", { method: "POST" }),
  onvifStreams: (onvif_url: string, username: string, password: string) =>
    request<{ streams: OnvifStream[] }>("/api/cameras/discover/onvif", {
      method: "POST", body: JSON.stringify({ onvif_url, username, password }) }),
  probeStream: (stream_url: string) =>
    request<CameraTestResult>("/api/cameras/probe", { method: "POST", body: JSON.stringify({ stream_url }) }),
  muteCamera: (id: number, minutes: number) =>
    request<Camera>(`/api/cameras/${id}/mute`, { method: "PUT", body: JSON.stringify({ minutes }) }),
  testCamera: (id: number) => request<CameraTestResult>(`/api/cameras/${id}/test`, { method: "POST" }),

  events: (f: EventFilters) => request<EventPage>(`/api/events${query({ ...f })}`),
  event: (id: number | string) => request<VisionEvent>(`/api/events/${id}`),
  updateEvent: (id: number, body: { status?: EventStatus; note?: string; reviewed_by?: string }) =>
    request<VisionEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  summary: (hours = 24) => request<Summary>(`/api/analytics/summary${query({ hours })}`),
  timeseries: (hours: number, bucket: "hour" | "day", camera_id?: string) =>
    request<Timeseries>(`/api/analytics/timeseries${query({ hours, bucket, camera_id })}`),
  rulePerformance: (hours: number) => request<RulePerformance[]>(`/api/analytics/rules${query({ hours })}`),
  entries: (hours: number, bucket: "hour" | "day", camera_id?: string) =>
    request<Entries>(`/api/analytics/entries${query({ hours, bucket, camera_id })}`),
  entriesCsvUrl: (hours: number, bucket: "hour" | "day", camera_id?: string) =>
    `/api/analytics/entries.csv${query({ hours, bucket, camera_id })}`,
  eventsCsvUrl: (f: EventFilters) => `/api/events/export.csv${query({ ...f, limit: undefined, offset: undefined })}`,

  authStatus: () => request<AuthStatus>("/api/auth/status"),
  setup: (username: string, password: string) =>
    request<User>("/api/auth/setup", { method: "POST", body: JSON.stringify({ username, password }) }),
  login: (username: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("/api/auth/password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }),

  users: () => request<User[]>("/api/users"),
  createUser: (body: { username: string; password: string; role: Role }) =>
    request<User>("/api/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: number, body: { role?: Role; active?: boolean; password?: string }) =>
    request<User>(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  audit: (limit = 50, offset = 0) =>
    request<{ total: number; items: AuditEntry[] }>(`/api/audit${query({ limit, offset })}`),

  rules: (cameraId: number) => request<Rule[]>(`/api/cameras/${cameraId}/rules`),
  createRule: (cameraId: number, body: Partial<RuleInput>) =>
    request<Rule>(`/api/cameras/${cameraId}/rules`, { method: "POST", body: JSON.stringify(body) }),
  updateRule: (id: number, body: Partial<RuleInput>) =>
    request<Rule>(`/api/rules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRule: (id: number) => request<void>(`/api/rules/${id}`, { method: "DELETE" }),
  frameUrl: (cameraId: number) => `/api/cameras/${cameraId}/frame?t=${Date.now()}`,

  search: (p: { q?: string; camera_id?: string; object_class?: string; hours?: number }) =>
    request<Sighting[]>(`/api/search${query({ ...p })}`),
  searchStatus: () => request<{ available: boolean; enabled: boolean }>("/api/search/status"),
  sighting: (id: number) => request<SightingDetail>(`/api/sightings/${id}`),
  similar: (id: number, hours: number) => request<Sighting[]>(`/api/sightings/${id}/similar${query({ hours })}`),
  arming: () => request<Arming>("/api/arming"),
  setArming: (armed: boolean) => request<Arming>("/api/arming", { method: "PUT", body: JSON.stringify({ armed }) }),
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (body: Partial<AppSettings>) =>
    request<AppSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  system: () => request<SystemHealth>("/api/system"),
  notifications: () => request<NotificationSettings>("/api/settings/notifications"),
  updateNotifications: (body: NotificationUpdate) =>
    request<NotificationSettings>("/api/settings/notifications", { method: "PATCH", body: JSON.stringify(body) }),
  sendSummary: () =>
    request<{ ok: boolean; error: string | null }>("/api/settings/notifications/summary", { method: "POST" }),
  testNotification: () =>
    request<{ ok: boolean; error: string | null }>("/api/settings/notifications/test", { method: "POST" }),
};
