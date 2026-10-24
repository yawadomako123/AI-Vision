const EVENT_LABELS: Record<string, string> = {
  zone_entry: "Zone entry",
  line_crossing: "Line crossing",
  loitering: "Loitering",
  occupancy_exceeded: "Occupancy exceeded",
  camera_offline: "Camera offline",
  camera_online: "Camera back online",
  camera_tamper: "Camera tampering",
  camera_tamper_cleared: "Camera view back to normal",
  plate_watchlist: "Watched plate seen",
};

export const EVENT_TYPES = Object.keys(EVENT_LABELS);

export function eventLabel(type: string): string {
  return EVENT_LABELS[type] ?? type.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const STATUS_LABELS: Record<string, string> = {
  unreviewed: "Unreviewed",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_alarm: "False alarm",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
const time = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

export function formatTime(iso: string): string {
  return time.format(new Date(iso));
}

export function timeAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour");
  return relative.format(Math.round(seconds / 86400), "day");
}

/** Browser live view for streams that go through the local MediaMTX (RTSP :8554 -> WebRTC :8889). */
export function liveViewUrl(maskedStreamUrl: string, livePath?: string | null): string | null {
  // IP cameras are proxied through MediaMTX (as aiv<id>), so they have live video too.
  if (livePath) return `${location.protocol}//${location.hostname}:8889/${livePath}`;
  try {
    const url = new URL(maskedStreamUrl);
    // "mediamtx" is the service name when everything runs in Docker Compose.
    const local = ["127.0.0.1", "localhost", "mediamtx", location.hostname].includes(url.hostname);
    if (url.protocol !== "rtsp:" || url.port !== "8554" || !local) return null;
    return `${location.protocol}//${location.hostname}:8889${url.pathname}`;
  } catch {
    return null;
  }
}
