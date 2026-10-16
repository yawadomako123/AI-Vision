import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

import { api, type EventFilters } from "../api";
import { EventStatusBadge, SeverityBadge } from "../components/Badges";
import { EventDetail } from "../components/EventDetail";
import { Modal } from "../components/Modal";
import { Button, Card, Empty, ErrorNote, selectCls } from "../components/ui";
import { EVENT_TYPES, eventLabel, formatDateTime, statusLabel, timeAgo } from "../format";

const PAGE = 25;
const FILTER_KEYS = ["camera_id", "event_type", "status", "severity"] as const;

export default function Events() {
  // Filters, page and the open event live in the URL, so views can be bookmarked and shared.
  const [params, setParams] = useSearchParams();
  const page = Math.max(0, Number(params.get("page") ?? 0));
  const openId = params.get("open");
  const filters: EventFilters = { limit: PAGE, offset: page * PAGE };
  for (const k of FILTER_KEYS) filters[k] = params.get(k) ?? undefined;

  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const events = useQuery({
    queryKey: ["events", filters],
    queryFn: () => api.events(filters),
    placeholderData: keepPreviousData,
  });

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };
  const setFilter = (k: string, v: string) => update({ [k]: v || null, page: null });

  const total = events.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const opened = events.data?.items.find((e) => String(e.id) === openId);
  // Opened from a link (toast, overview) and not on the current page: fetch it directly.
  const openedQuery = useQuery({
    queryKey: ["events", "one", openId],
    queryFn: () => api.event(openId!),
    enabled: !!openId && !opened,
  });
  const detail = opened ?? openedQuery.data ?? null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold">Events</h1>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filters">
          <select aria-label="Camera" className={selectCls} value={params.get("camera_id") ?? ""} onChange={(e) => setFilter("camera_id", e.target.value)}>
            <option value="">All cameras</option>
            {cameras.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select aria-label="Event type" className={selectCls} value={params.get("event_type") ?? ""} onChange={(e) => setFilter("event_type", e.target.value)}>
            <option value="">All types</option>
            {EVENT_TYPES.map((t) => <option key={t} value={t}>{eventLabel(t)}</option>)}
          </select>
          <select aria-label="Status" className={selectCls} value={params.get("status") ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">Any status</option>
            {["unreviewed", "acknowledged", "resolved", "false_alarm"].map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
          </select>
          <select aria-label="Severity" className={selectCls} value={params.get("severity") ?? ""} onChange={(e) => setFilter("severity", e.target.value)}>
            <option value="">Any severity</option>
            {["high", "medium", "low"].map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}
          </select>
        </div>
      </div>

      {events.error && <ErrorNote error={events.error} />}

      <Card>
        {events.data?.items.length === 0 && <Empty>No events match these filters.</Empty>}
        {!!events.data?.items.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line text-xs text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Snapshot</th>
                  <th className="px-3 py-2.5 font-medium">Event</th>
                  <th className="px-3 py-2.5 font-medium">Camera</th>
                  <th className="px-3 py-2.5 font-medium">When</th>
                  <th className="px-3 py-2.5 font-medium">Severity</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {events.data.items.map((e) => (
                  <tr key={e.id} className="cursor-pointer hover:bg-surface-2" onClick={() => update({ open: String(e.id) })}>
                    <td className="px-4 py-2">
                      {e.snapshot_url ? (
                        <img src={e.snapshot_url} alt="" loading="lazy" className="h-12 w-20 rounded-md border border-line bg-black object-cover" />
                      ) : (
                        <div className="h-12 w-20 rounded-md border border-dashed border-line" />
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {/* Real button so the row is reachable by keyboard */}
                      <button type="button" className="text-left font-medium hover:text-accent" onClick={(ev) => { ev.stopPropagation(); update({ open: String(e.id) }); }}>
                        {eventLabel(e.event_type)}
                      </button>
                      {e.rule_name && <p className="text-xs text-muted">{e.rule_name}</p>}
                    </td>
                    <td className="px-3 py-2">{e.camera_name}</td>
                    <td className="tabular px-3 py-2 whitespace-nowrap" title={formatDateTime(e.occurred_at)}>{timeAgo(e.occurred_at)}</td>
                    <td className="px-3 py-2"><SeverityBadge severity={e.severity} /></td>
                    <td className="px-3 py-2"><EventStatusBadge status={e.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE && (
          <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-sm">
            <span className="tabular text-muted">{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
            <div className="flex gap-2">
              <Button disabled={page === 0} onClick={() => update({ page: page > 1 ? String(page - 1) : null })}>Previous</Button>
              <Button disabled={page + 1 >= pages} onClick={() => update({ page: String(page + 1) })}>Next</Button>
            </div>
          </div>
        )}
      </Card>

      <Modal open={!!detail} onClose={() => update({ open: null })} wide
             title={detail ? `${eventLabel(detail.event_type)} · ${detail.camera_name ?? ""}` : "Event"}>
        {detail && <EventDetail key={detail.id} event={detail} onDone={() => update({ open: null })} />}
      </Modal>
    </div>
  );
}
