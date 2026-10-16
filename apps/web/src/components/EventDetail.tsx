import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type EventStatus, type VisionEvent } from "../api";
import { eventLabel, formatDateTime } from "../format";
import { EventStatusBadge, SeverityBadge } from "./Badges";
import { Button, ErrorNote, inputCls } from "./ui";

export function EventDetail({ event, onDone }: { event: VisionEvent; onDone: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState(event.note ?? "");

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
        {event.snapshot_url ? (
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
