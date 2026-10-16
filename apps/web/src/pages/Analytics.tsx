import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

import { api } from "../api";
import { DataTable, Legend, LineChart, StackedBars, type Series } from "../components/charts";
import { Card, Empty, ErrorNote, selectCls } from "../components/ui";
import { EVENT_TYPES, eventLabel } from "../format";

const RANGES = {
  "24h": { hours: 24, bucket: "hour", label: "Last 24 hours" },
  "7d": { hours: 24 * 7, bucket: "day", label: "Last 7 days" },
  "30d": { hours: 24 * 30, bucket: "day", label: "Last 30 days" },
} as const;
type RangeKey = keyof typeof RANGES;

// Color follows the entity: each event type keeps its slot whatever is filtered.
const EVENT_COLOR: Record<string, string> = Object.fromEntries(
  EVENT_TYPES.map((t, i) => [t, `var(--series-${i + 1})`]),
);

const hourFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const fullFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="tabular mt-1 text-2xl font-semibold">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

export default function Analytics() {
  const [params, setParams] = useSearchParams();
  const rangeKey = (params.get("range") as RangeKey) in RANGES ? (params.get("range") as RangeKey) : "24h";
  const range = RANGES[rangeKey];
  const cameraId = params.get("camera_id") ?? "";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };

  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const ts = useQuery({
    queryKey: ["timeseries", range.hours, range.bucket, cameraId],
    queryFn: () => api.timeseries(range.hours, range.bucket, cameraId || undefined),
    placeholderData: keepPreviousData,
  });

  const d = ts.data;
  const dates = d?.buckets.map((b) => new Date(b)) ?? [];
  const labels = dates.map((t) => (range.bucket === "hour" ? hourFmt.format(t) : dayFmt.format(t)));
  const tooltipLabels = dates.map((t) => (range.bucket === "hour" ? fullFmt.format(t) : dayFmt.format(t)));

  const zeros = labels.map(() => 0);
  const people = d?.counts.person ?? { peak: zeros, avg: zeros };
  const vehicles = d?.counts.vehicle ?? { peak: zeros, avg: zeros };
  const countSeries: Series[] = [
    { key: "person", label: "People", color: "var(--series-1)", values: people.peak,
      note: (i) => `avg ${people.avg[i]} people` },
    { key: "vehicle", label: "Vehicles", color: "var(--series-2)", values: vehicles.peak,
      note: (i) => `avg ${vehicles.avg[i]} vehicles` },
  ];
  const eventSeries: Series[] = EVENT_TYPES.filter((t) => d?.events[t]).map((t) => ({
    key: t, label: eventLabel(t), color: EVENT_COLOR[t], values: d!.events[t],
  }));

  const totalEvents = eventSeries.reduce((s, x) => s + x.values.reduce((a, b) => a + b, 0), 0);
  const crossings = (d?.events.line_crossing ?? []).reduce((a, b) => a + b, 0);
  const peakPeople = Math.max(0, ...people.peak);
  const peakAt = people.peak.indexOf(peakPeople);
  const hasCounts = (d?.counts.person || d?.counts.vehicle) !== undefined;
  const since = new Date(Date.now() - range.hours * 3600_000).toISOString();

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold">Analytics</h1>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filters">
          <select aria-label="Time range" className={selectCls} value={rangeKey} onChange={(e) => setParam("range", e.target.value)}>
            {Object.entries(RANGES).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
          </select>
          <select aria-label="Camera" className={selectCls} value={cameraId} onChange={(e) => setParam("camera_id", e.target.value)}>
            <option value="">All cameras</option>
            {cameras.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <a className="inline-flex items-center rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2"
             href={api.eventsCsvUrl({ camera_id: cameraId || undefined, since })} download>
            Export events (CSV)
          </a>
        </div>
      </div>
      {ts.error && <ErrorNote error={ts.error} />}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Most people at once" value={peakPeople}
              sub={peakPeople > 0 && peakAt >= 0 ? tooltipLabels[peakAt] : undefined} />
        <Stat label="Most vehicles at once" value={Math.max(0, ...vehicles.peak)} />
        <Stat label="Events" value={totalEvents} sub={range.label.toLowerCase()} />
        <Stat label="Line crossings" value={crossings} sub="entries and exits" />
      </div>

      <Card title="People and vehicles in view (most at once)">
        <div className="flex flex-col gap-3 p-4">
          {!hasCounts && <Empty>No counts yet. They appear once the vision worker has been running for a minute.</Empty>}
          {hasCounts && (
            <>
              <Legend series={countSeries} />
              <LineChart labels={labels} tooltipLabels={tooltipLabels} series={countSeries}
                         label={`People and vehicles in view per ${range.bucket}, ${range.label.toLowerCase()}`} />
              <DataTable labels={tooltipLabels} series={countSeries} />
            </>
          )}
        </div>
      </Card>

      <Card title="Events by type">
        <div className="flex flex-col gap-3 p-4">
          {eventSeries.length === 0 ? <Empty>No events in this period.</Empty> : (
            <>
              <Legend series={eventSeries} />
              <StackedBars labels={labels} tooltipLabels={tooltipLabels} series={eventSeries}
                           label={`Events per ${range.bucket} by type, ${range.label.toLowerCase()}`} />
              <DataTable labels={tooltipLabels} series={eventSeries} />
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
