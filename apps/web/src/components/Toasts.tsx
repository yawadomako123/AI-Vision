import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { VisionEvent } from "../api";
import { eventLabel, formatTime } from "../format";
import { useLive } from "../live";

/** Pops a short notice for each new event pushed by the server. */
export function EventToasts() {
  const { onEvent } = useLive();
  const [toasts, setToasts] = useState<VisionEvent[]>([]);

  useEffect(
    () =>
      onEvent((e) => {
        setToasts((t) => [e, ...t].slice(0, 4));
        window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== e.id)), 8000);
      }),
    [onEvent],
  );

  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 max-w-[calc(100%-2rem)] flex-col gap-2">
      {toasts.map((e) => (
        <Link
          key={e.id}
          to={`/events?open=${e.id}`}
          onClick={() => setToasts((t) => t.filter((x) => x.id !== e.id))}
          className={`pointer-events-auto rounded-lg border bg-surface px-4 py-3 text-sm shadow-lg ${
            e.severity === "high" ? "border-bad/60" : "border-line"
          }`}
        >
          <span className="font-semibold">{eventLabel(e.event_type)}</span>
          <span className="text-muted"> · {e.camera_name}</span>
          <span className="block text-xs text-muted">
            {formatTime(e.occurred_at)}
            {e.rule_name && ` · ${e.rule_name}`}
          </span>
        </Link>
      ))}
    </div>
  );
}
