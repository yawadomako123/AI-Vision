import type { CameraStatus, EventStatus, Severity } from "../api";
import { statusLabel } from "../format";

const pill = "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap";

const CAMERA: Record<CameraStatus, { cls: string; label: string }> = {
  online: { cls: "bg-ok/15 text-ok", label: "Online" },
  offline: { cls: "bg-bad/15 text-bad", label: "Offline" },
  connecting: { cls: "bg-warn/15 text-warn", label: "Connecting" },
  unknown: { cls: "bg-surface-2 text-muted", label: "Unknown" },
};

export function CameraStatusBadge({ status, enabled = true }: { status: CameraStatus; enabled?: boolean }) {
  if (!enabled) return <span className={`${pill} bg-surface-2 text-muted`}>Disabled</span>;
  const s = CAMERA[status] ?? CAMERA.unknown;
  return (
    <span className={`${pill} ${s.cls}`}>
      <span className={`size-1.5 rounded-full bg-current ${status === "online" ? "animate-pulse" : ""}`} aria-hidden />
      {s.label}
    </span>
  );
}

const SEVERITY: Record<Severity, string> = {
  high: "bg-bad/15 text-bad",
  medium: "bg-warn/15 text-warn",
  low: "bg-surface-2 text-muted",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`${pill} ${SEVERITY[severity] ?? SEVERITY.low} capitalize`}>{severity}</span>;
}

const EVENT_STATUS: Record<EventStatus, string> = {
  unreviewed: "bg-accent/15 text-accent",
  acknowledged: "bg-warn/15 text-warn",
  resolved: "bg-ok/15 text-ok",
  false_alarm: "bg-surface-2 text-muted",
};

export function EventStatusBadge({ status }: { status: EventStatus }) {
  return <span className={`${pill} ${EVENT_STATUS[status]}`}>{statusLabel(status)}</span>;
}
