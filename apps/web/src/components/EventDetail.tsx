import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type EventStatus, type VisionEvent } from "../api";
import { eventLabel, formatDateTime } from "../format";
import { EventStatusBadge, SeverityBadge } from "./Badges";
import { Button, ErrorNote, inputCls } from "./ui";

/** Plays the alert's short clip. It is encoded a few seconds after the alert, so retry briefly. */
function ClipPlayer({ src, poster }: { src: string; poster: string | null }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "pending" | "gone">("loading");
  useEffect(() => {
    if (state !== "pending") return;
    if (attempt >= 8) { setState("gone"); return; }
    const t = window.setTimeout(() => { setAttempt((a) => a + 1); setState("loading"); }, 3000);
    return () => window.clearTimeout(t);
  }, [state, attempt]);
  if (state === "gone") {
    return <p className="grid aspect-video place-items-center rounded-lg border border-dashed border-line text-sm text-muted">
      Clip not available (it may have been removed by the storage limit).
    </p>;
  }
  return (
    <div className="relative">
      <video key={attempt} src={`${src}${attempt ? `?r=${attempt}` : ""}`} poster={poster ?? undefined}
             controls autoPlay muted playsInline preload="auto"
             onCanPlay={() => setState("ready")} onError={() => setState("pending")}
             className="w-full rounded-lg border border-line bg-black" />
      {state === "pending" && (
        <p className="absolute inset-x-0 bottom-12 mx-auto w-fit rounded-md bg-black/70 px-3 py-1.5 text-xs text-white">
          Preparing clip…
        </p>
      )}
    </div>
  );
}

export function EventDetail({ event, onDone }: { event: VisionEvent; onDone: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState(event.note ?? "");
  const [view, setView] = useState<"clip" | "snapshot">("clip");

  // The reviewer is recorded by the server from the signed-in account.
  const review = useMutation({
    mutationFn: (status: EventStatus) => api.updateEvent(event.id, { status, note: note || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["events"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      onDone();
    },
  });

  const details = Object.entries(event.details).filter(([k]) => k !== "box");

  return (
    <div className="grid gap-5 md:grid-cols-[1fr_18rem]">
      <div>
        {event.clip_url && view === "clip" ? (
          <ClipPlayer src={event.clip_url} poster={event.snapshot_url} />
        ) : event.snapshot_url ? (
          <a href={event.snapshot_url} target="_blank" rel="noreferrer" title="Open full size">
            <img
              src={event.snapshot_url}
              alt={`Snapshot: ${eventLabel(event.event_type)} on ${event.camera_name ?? "camera"}`}
              className="w-full rounded-lg border border-line bg-black object-contain"
            />
          </a>
        ) : (
          <div className="grid aspect-video place-items-center rounded-lg border border-dashed border-line text-sm text-muted">
            No snapshot for this event
          </div>
        )}
        {event.clip_url && event.snapshot_url && (
          <div className="mt-2 flex gap-1 text-xs" role="group" aria-label="Evidence">
            {(["clip", "snapshot"] as const).map((v) => (
              <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}
                      className={`rounded-md px-2.5 py-1 font-medium ${view === v ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2"}`}>
                {v === "clip" ? "Clip" : "Snapshot"}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-4 text-sm">
        <div className="flex flex-wrap gap-2">
          <SeverityBadge severity={event.severity} />
          <EventStatusBadge status={event.status} />
        </div>

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
          <dt className="text-muted">When</dt>
          <dd className="tabular">{formatDateTime(event.occurred_at)}</dd>
          <dt className="text-muted">Camera</dt>
          <dd>{event.camera_name ?? `#${event.camera_id}`}</dd>
          {event.rule_name && (
            <>
              <dt className="text-muted">Rule</dt>
              <dd>{event.rule_name}</dd>
            </>
          )}
          {event.object_class && (
            <>
              <dt className="text-muted">Object</dt>
              <dd>
                {event.object_class}
                {event.track_id != null && <span className="text-muted"> #{event.track_id}</span>}
                {event.confidence != null && (
                  <span className="text-muted"> · {Math.round(event.confidence * 100)}% detection</span>
                )}
              </dd>
            </>
          )}
          {details.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
              <dd className="tabular">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
            </div>
          ))}
          {event.reviewed_at && (
            <>
              <dt className="text-muted">Reviewed</dt>
              <dd>
                {formatDateTime(event.reviewed_at)}
                {event.reviewed_by && ` by ${event.reviewed_by}`}
              </dd>
            </>
          )}
        </dl>

        {event.object_class && (
          <p className="text-xs text-muted">
            Detection confidence is how sure the model is that it saw a {event.object_class}, not a judgement that
            anything is a threat. A person should review every alert.
          </p>
        )}

        <div className="flex flex-col gap-2 border-t border-line pt-4">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Note</span>
            <textarea
              className={`${inputCls} min-h-16`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={2000}
              placeholder="What happened?"
            />
          </label>
          {review.error && <ErrorNote error={review.error} />}
          <div className="grid grid-cols-3 gap-2">
            <Button onClick={() => review.mutate("acknowledged")} disabled={review.isPending}>Acknowledge</Button>
            <Button variant="primary" onClick={() => review.mutate("resolved")} disabled={review.isPending}>
              Resolve
            </Button>
            <Button onClick={() => review.mutate("false_alarm")} disabled={review.isPending}>False alarm</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
