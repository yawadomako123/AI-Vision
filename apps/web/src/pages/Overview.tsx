import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "../api";
import { CameraStatusBadge, SeverityBadge } from "../components/Badges";
import { Card, Empty, ErrorNote } from "../components/ui";
import { eventLabel, timeAgo } from "../format";

function Stat({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className={`tabular mt-1 text-2xl font-semibold ${tone ?? ""}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

export default function Overview() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.summary(24) });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const recent = useQuery({ queryKey: ["events", { limit: 8 }], queryFn: () => api.events({ limit: 8 }) });

  const s = summary.data;
  const online = s?.cameras.by_status.online ?? 0;
  const offline = (s?.cameras.total ?? 0) - online;
  const byType = Object.entries(s?.events.by_type ?? {}).sort((a, b) => b[1] - a[1]);
  const maxType = Math.max(1, ...byType.map(([, n]) => n));

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold">Overview</h1>
      {summary.error && <ErrorNote error={summary.error} />}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Cameras online" value={s ? `${online} / ${s.cameras.total}` : "–"}
              tone={offline > 0 ? "text-warn" : "text-ok"} sub={offline > 0 ? `${offline} not online` : "All online"} />
        <Stat label="Events (24 h)" value={s?.events.total ?? "–"} />
        <Stat label="Needs review" value={s?.unreviewed ?? "–"} tone={s?.unreviewed ? "text-accent" : ""} />
        <Stat label="Most common (24 h)" value={byType[0] ? eventLabel(byType[0][0]) : "–"}
              sub={byType[0] ? `${byType[0][1]} events` : undefined} />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <Card title="Recent events" action={<Link to="/events" className="text-sm text-accent">View all</Link>}>
          {recent.data?.items.length === 0 && <Empty>No events yet. They appear here live as cameras report them.</Empty>}
          <ul className="divide-y divide-line">
            {recent.data?.items.map((e) => (
              <li key={e.id}>
                <Link to={`/events?open=${e.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                  {e.snapshot_url ? (
                    <img src={e.snapshot_url} alt="" loading="lazy"
                         className="h-12 w-20 shrink-0 rounded-md border border-line bg-black object-cover" />
                  ) : (
                    <div className="h-12 w-20 shrink-0 rounded-md border border-dashed border-line" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {eventLabel(e.event_type)}
                      {e.rule_name && <span className="font-normal text-muted"> · {e.rule_name}</span>}
                    </p>
                    <p className="truncate text-xs text-muted">{e.camera_name} · {timeAgo(e.occurred_at)}</p>
                  </div>
                  <SeverityBadge severity={e.severity} />
                </Link>
              </li>
            ))}
          </ul>
        </Card>

        <div className="flex flex-col gap-5">
          <Card title="Cameras" action={<Link to="/cameras" className="text-sm text-accent">Manage</Link>}>
            {cameras.data?.length === 0 && <Empty>No cameras yet. <Link to="/cameras" className="text-accent">Add one</Link>.</Empty>}
            <ul className="divide-y divide-line">
              {cameras.data?.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{c.name}</p>
                    <p className="text-xs text-muted">
                      {c.status === "online" && c.infer_fps != null ? `${c.infer_fps.toFixed(1)} fps analysed` : `Last seen ${timeAgo(c.last_seen_at)}`}
                    </p>
                  </div>
                  <CameraStatusBadge status={c.status} enabled={c.enabled} />
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Events by type (24 h)">
            {byType.length === 0 ? <Empty>Nothing in the last 24 hours.</Empty> : (
              <ul className="flex flex-col gap-2.5 px-4 py-3">
                {byType.map(([type, n]) => (
                  <li key={type} className="text-sm">
                    <div className="flex justify-between"><span>{eventLabel(type)}</span><span className="tabular text-muted">{n}</span></div>
                    <div className="mt-1 h-1.5 rounded-full bg-surface-2">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${(n / maxType) * 100}%` }} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
