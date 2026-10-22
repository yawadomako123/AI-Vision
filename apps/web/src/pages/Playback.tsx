import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { api } from "../api";
import { Button, Card, Empty, ErrorNote, inputCls, selectCls } from "../components/ui";
import { eventLabel } from "../format";

const CHUNK = 300; // seconds per request; the next chunk loads when one ends
const DAY_MS = 24 * 3600_000;
const SEVERITY_COLOR: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--accent)" };

const pad = (n: number) => String(n).padStart(2, "0");
const localDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const hourFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

interface BarProps {
  from: Date;
  to: Date;
  segments: { start: string; end: string }[];
  events: { id: number; occurred_at: string; severity: string; event_type: string }[];
  cursor: Date | null;
  ticks: { at: Date; label: string }[];
  onPick: (when: Date) => void;
  label: string;
}

function TimeBar({ from, to, segments, events, cursor, ticks, onPick, label }: BarProps) {
  const span = to.getTime() - from.getTime();
  const x = (d: Date) => Math.min(100, Math.max(0, ((d.getTime() - from.getTime()) / span) * 100));
  const inRange = (d: Date) => d >= from && d <= to;
  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-11 cursor-pointer rounded-lg bg-surface-2" role="slider" aria-label={label} tabIndex={0}
           aria-valuemin={0} aria-valuemax={100} aria-valuenow={cursor ? Math.round(x(cursor)) : 0}
           onClick={(e) => {
             const r = e.currentTarget.getBoundingClientRect();
             onPick(new Date(from.getTime() + ((e.clientX - r.left) / r.width) * span));
           }}>
        {segments.map((sg) => {
          const a = new Date(sg.start), b = new Date(sg.end);
          if (b < from || a > to) return null;
          return <div key={sg.start} className="absolute inset-y-2 rounded-sm bg-accent/40"
                      style={{ left: `${x(a)}%`, width: `${Math.max(0.3, x(b) - x(a))}%` }} />;
        })}
        {events.filter((e) => inRange(new Date(e.occurred_at))).map((e) => (
          <button key={e.id} type="button" title={`${eventLabel(e.event_type)} · ${timeFmt.format(new Date(e.occurred_at))}`}
                  onClick={(ev) => { ev.stopPropagation(); onPick(new Date(new Date(e.occurred_at).getTime() - 10_000)); }}
                  className="absolute inset-y-0 w-1 -translate-x-1/2 rounded-full"
                  style={{ left: `${x(new Date(e.occurred_at))}%`, background: SEVERITY_COLOR[e.severity] ?? "var(--muted)" }} />
        ))}
        {cursor && inRange(cursor) && <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-text" style={{ left: `${x(cursor)}%` }} />}
      </div>
      <div className="relative h-4 text-[11px] text-muted tabular" aria-hidden>
        {ticks.map((t, i) => (
          <span key={t.at.getTime()} className={`absolute whitespace-nowrap ${i === 0 ? "" : i === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2"}`}
                style={{ left: `${x(t.at)}%` }}>{t.label}</span>
        ))}
      </div>
    </div>
  );
}

/** Watch recordings: a 24-hour bar per day showing what was recorded and when alerts happened. */
export default function Playback() {
  const [params, setParams] = useSearchParams();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const cameraId = Number(params.get("camera") ?? cameras.data?.[0]?.id ?? 0);
  const t = params.get("t") ? new Date(params.get("t")!) : null;
  const day = params.get("day") ?? localDay(t ?? new Date());
  const dayStart = useMemo(() => new Date(`${day}T00:00:00`), [day]);
  const dayEnd = useMemo(() => new Date(dayStart.getTime() + DAY_MS), [dayStart]);

  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };
  const playAt = (when: Date) => set({ t: when.toISOString(), day: localDay(when) });

  const recordings = useQuery({
    queryKey: ["recordings", cameraId, day],
    queryFn: () => api.recordings(cameraId, dayStart.toISOString(), dayEnd.toISOString()),
    enabled: cameraId > 0,
    refetchInterval: 60_000,
  });
  const events = useQuery({
    queryKey: ["events", "playback", cameraId, day],
    queryFn: () => api.events({ camera_id: String(cameraId), since: dayStart.toISOString(), until: dayEnd.toISOString(), limit: 200 }),
    enabled: cameraId > 0,
  });

  const video = useRef<HTMLVideoElement>(null);
  const [chunkStart, setChunkStart] = useState<Date | null>(t);
  useEffect(() => setChunkStart(t), [params.get("t")]); // eslint-disable-line react-hooks/exhaustive-deps
  const [now, setNow] = useState<Date | null>(null); // current playback time, for the cursor
  const src = chunkStart && cameraId ? api.playbackUrl(cameraId, chunkStart.toISOString(), CHUNK) : null;

  const shiftDay = (days: number) => set({ day: localDay(new Date(dayStart.getTime() + days * DAY_MS + 3600_000)), t: null });
  const segments = recordings.data?.segments ?? [];
  const evs = events.data?.items ?? [];
  // The zoomed bar shows the hour around what you're watching, else around the latest recording.
  const focus = now ?? chunkStart ?? (segments.length ? new Date(segments[segments.length - 1].end) : new Date(dayStart.getTime() + 12 * 3600_000));
  const zoomFrom = new Date(Math.min(Math.max(focus.getTime() - 30 * 60_000, dayStart.getTime()), dayEnd.getTime() - 3600_000));
  const zoomTo = new Date(zoomFrom.getTime() + 3600_000);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold">Playback</h1>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Camera" className={selectCls} value={cameraId || ""} onChange={(e) => set({ camera: e.target.value, t: null })}>
            {cameras.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <span className="flex items-center gap-1">
            <Button onClick={() => shiftDay(-1)} aria-label="Previous day">‹</Button>
            <input type="date" className={`${inputCls} w-40`} aria-label="Day" value={day} onChange={(e) => e.target.value && set({ day: e.target.value, t: null })} />
            <Button onClick={() => shiftDay(1)} aria-label="Next day" disabled={day >= localDay(new Date())}>›</Button>
          </span>
        </div>
      </div>

      {recordings.error && <ErrorNote error={recordings.error} />}

      <Card>
        <div className="flex flex-col gap-2 p-4">
          <TimeBar label="Time of day" from={dayStart} to={dayEnd} segments={segments} events={evs} cursor={now ?? chunkStart}
                   onPick={playAt} ticks={[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => ({ at: new Date(dayStart.getTime() + h * 3600_000), label: `${pad(h % 24)}:00` }))} />
          <p className="mt-1 text-xs font-medium text-muted">Zoom: {hourFmt.format(zoomFrom)}–{hourFmt.format(zoomTo)}</p>
          <TimeBar label="Zoomed hour" from={zoomFrom} to={zoomTo} segments={segments} events={evs} cursor={now ?? chunkStart}
                   onPick={playAt} ticks={[0, 10, 20, 30, 40, 50, 60].map((m) => { const at = new Date(zoomFrom.getTime() + m * 60_000); return { at, label: hourFmt.format(at) }; })} />
          <p className="text-xs text-muted">
            Shaded = recorded. Coloured marks = alerts (click one to watch from 10 s before). Click anywhere on either bar to play from that time.
          </p>
        </div>
      </Card>

      <Card>
        {src ? (
          <div className="flex flex-col gap-2 p-4">
            <video key={src} ref={video} src={src} controls autoPlay muted playsInline className="w-full rounded-lg bg-black"
                   onTimeUpdate={(e) => chunkStart && setNow(new Date(chunkStart.getTime() + e.currentTarget.currentTime * 1000))}
                   onEnded={() => chunkStart && setChunkStart(new Date(chunkStart.getTime() + CHUNK * 1000))} />
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="tabular font-medium">{now ? timeFmt.format(now) : timeFmt.format(chunkStart!)}</span>
              <span className="flex-1" />
              {[-60, -10, 10, 60].map((d) => (
                <Button key={d} onClick={() => playAt(new Date((now ?? chunkStart!).getTime() + d * 1000))}>
                  {d > 0 ? "+" : "−"}{Math.abs(d) >= 60 ? `${Math.abs(d) / 60} min` : `${Math.abs(d)} s`}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted">If nothing plays, nothing was recorded at that moment: pick a shaded part of the bar.</p>
          </div>
        ) : (
          <Empty>{segments.length ? "Pick a time on the bar above, or an alert." : "Nothing recorded on this day. Recording is set in Settings → Recording."}</Empty>
        )}
      </Card>

      {evs.length > 0 && (
        <Card title={`Alerts on this day (${evs.length})`}>
          <ul className="max-h-72 divide-y divide-line overflow-y-auto">
            {evs.map((e) => (
              <li key={e.id}>
                <button type="button" onClick={() => playAt(new Date(new Date(e.occurred_at).getTime() - 10_000))}
                        className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm hover:bg-surface-2">
                  <span className="size-2 shrink-0 rounded-full" style={{ background: SEVERITY_COLOR[e.severity] ?? "var(--muted)" }} />
                  <span className="tabular text-muted">{timeFmt.format(new Date(e.occurred_at))}</span>
                  <span className="min-w-0 flex-1 truncate">{eventLabel(e.event_type)}{e.rule_name && <span className="text-muted"> · {e.rule_name}</span>}</span>
                  <span className="text-xs text-accent">Watch</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
