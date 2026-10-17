import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";

import { api, type Sighting } from "../api";
import { ClipPlayer } from "../components/EventDetail";
import { Modal } from "../components/Modal";
import { Button, Card, Empty, ErrorNote, inputCls, selectCls } from "../components/ui";
import { eventLabel, formatDateTime, formatTime, timeAgo } from "../format";

const EXAMPLES = ["person in a white hoodie", "someone carrying a bag", "person on a bicycle", "white car", "delivery driver"];
const RANGES = { "24": "Last 24 hours", "168": "Last 7 days", "720": "Last 30 days" } as const;

function duration(s: number): string {
  if (s < 60) return `${Math.max(1, Math.round(s))} s`;
  return `${Math.round(s / 60)} min`;
}

function Relevance({ score }: { score: number }) {
  const label = score >= 0.66 ? "Strong match" : score >= 0.33 ? "Possible match" : "Weak match";
  return (
    <div className="flex items-center gap-2 text-xs text-muted" title={`${Math.round(score * 100)}% relevance`}>
      <div className="h-1.5 w-16 rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(6, score * 100)}%` }} />
      </div>
      {label}
    </div>
  );
}

function SightingCard({ s, onOpen }: { s: Sighting; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen}
            className="flex flex-col overflow-hidden rounded-xl border border-line bg-surface text-left hover:border-accent">
      <img src={s.thumbnail_url} alt={`${s.object_class} seen on ${s.camera_name}`} loading="lazy"
           className="h-44 w-full bg-black object-contain" />
      <div className="flex flex-col gap-1 p-3 text-sm">
        <span className="font-medium capitalize">{s.object_class} · {s.camera_name}</span>
        <span className="text-xs text-muted" title={formatDateTime(s.first_seen)}>
          {timeAgo(s.first_seen)} · in view {duration(s.duration_seconds)}
        </span>
        {s.score !== null && <Relevance score={s.score} />}
      </div>
    </button>
  );
}

function SightingDialog({ id, hours, onOpen }: { id: number; hours: number; onOpen: (id: number) => void }) {
  const detail = useQuery({ queryKey: ["sighting", id], queryFn: () => api.sighting(id) });
  const [showSimilar, setShowSimilar] = useState(false);
  const similar = useQuery({ queryKey: ["similar", id, hours], queryFn: () => api.similar(id, hours), enabled: showSimilar });
  useEffect(() => setShowSimilar(false), [id]);
  const d = detail.data;
  if (detail.error) return <ErrorNote error={detail.error} />;
  if (!d) return <p className="text-sm text-muted">Loading…</p>;
  const clip = d.clips.find((c) => c.clip_url);

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 md:grid-cols-[1fr_12rem]">
        <figure className="flex flex-col gap-1">
          {clip?.clip_url ? <ClipPlayer src={clip.clip_url} poster={d.context_url} /> : (
            <img src={d.context_url} alt="Where it was seen, with its path" className="w-full rounded-lg border border-line bg-black" />
          )}
          <figcaption className="text-xs text-muted">
            {clip ? `Clip from the ${eventLabel(clip.event_type).toLowerCase()} alert at ${formatTime(clip.occurred_at)}.`
              : "Camera view at the best moment. Yellow line = path walked (green start, red end). No alert fired, so there's no clip."}
          </figcaption>
        </figure>
        <div className="flex flex-col gap-3 text-sm">
          <img src={d.thumbnail_url} alt="Cut-out" className="max-h-56 w-full rounded-lg border border-line bg-black object-contain" />
          <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1">
            <dt className="text-muted">Camera</dt><dd>{d.camera_name}</dd>
            <dt className="text-muted">From</dt><dd className="tabular">{formatDateTime(d.first_seen)}</dd>
            <dt className="text-muted">In view</dt><dd>{duration(d.duration_seconds)}</dd>
            <dt className="text-muted">Type</dt><dd className="capitalize">{d.object_class} ({Math.round(d.confidence * 100)}%)</dd>
          </dl>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-line pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted">
            Find other sightings that <strong>look like this</strong> (clothing, shape, colour), on any camera, in time order.
            It's a lead, not an identification.
          </p>
          <Button variant="primary" onClick={() => setShowSimilar(true)} disabled={showSimilar}>Find similar</Button>
        </div>
        {similar.error && <ErrorNote error={similar.error} />}
        {showSimilar && similar.data && (
          similar.data.length <= 1 ? <Empty>No other sightings look like this in this period.</Empty> : (
            <ol className="relative flex flex-col gap-2 border-l-2 border-line pl-4">
              {similar.data.map((s) => (
                <li key={s.id} className="relative">
                  <span className={`absolute top-4 -left-[1.4rem] size-3 rounded-full ${s.id === id ? "bg-accent" : "bg-muted"}`} aria-hidden />
                  <button type="button" onClick={() => onOpen(s.id)} disabled={s.id === id}
                          className="flex w-full items-center gap-3 rounded-lg p-2 text-left hover:bg-surface-2 disabled:cursor-default disabled:hover:bg-transparent">
                    <img src={s.thumbnail_url} alt="" loading="lazy" className="h-14 w-12 rounded bg-black object-contain" />
                    <div className="min-w-0 flex-1 text-sm">
                      <p className="font-medium">{s.camera_name}{s.id === id && <span className="text-muted"> (this one)</span>}</p>
                      <p className="tabular text-xs text-muted">{formatDateTime(s.first_seen)} · {duration(s.duration_seconds)}</p>
                    </div>
                    {s.score !== null && s.id !== id && <Relevance score={s.score} />}
                  </button>
                </li>
              ))}
            </ol>
          )
        )}
      </div>
    </div>
  );
}

export default function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const hours = Number(params.get("hours") ?? 168);
  const cameraId = params.get("camera_id") ?? "";
  const objectClass = params.get("type") ?? "";
  const openId = params.get("open");
  const [text, setText] = useState(q);
  useEffect(() => setText(q), [q]);

  const status = useQuery({ queryKey: ["search-status"], queryFn: api.searchStatus, staleTime: 60_000 });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const results = useQuery({
    queryKey: ["search", q, hours, cameraId, objectClass],
    queryFn: () => api.search({ q, hours, camera_id: cameraId || undefined, object_class: objectClass || undefined }),
    placeholderData: keepPreviousData,
    enabled: status.data?.enabled === true && (status.data.available || !q),
  });

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: !("q" in changes) });
  };
  const submit = (e: FormEvent) => { e.preventDefault(); update({ q: text.trim() || null, open: null }); };

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold">Search</h1>
        <p className="text-sm text-muted">Describe what you're looking for. It searches everything the cameras saw, not just alerts.</p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-3" role="search">
        <div className="flex gap-2">
          <input className={`${inputCls} py-2.5 text-base`} value={text} onChange={(e) => setText(e.target.value)}
                 placeholder="e.g. person in a white hoodie carrying a bag" aria-label="Describe what to find" maxLength={200} />
          <Button type="submit" variant="primary" className="px-5">Search</Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" onClick={() => { setText(ex); update({ q: ex, open: null }); }}
                    className="rounded-full border border-line px-3 py-1 text-xs text-muted hover:border-accent hover:text-text">
              {ex}
            </button>
          ))}
          <span className="ml-auto flex flex-wrap gap-2">
            <select aria-label="Time range" className={selectCls} value={String(hours)} onChange={(e) => update({ hours: e.target.value })}>
              {Object.entries(RANGES).map(([h, label]) => <option key={h} value={h}>{label}</option>)}
            </select>
            <select aria-label="Camera" className={selectCls} value={cameraId} onChange={(e) => update({ camera_id: e.target.value || null })}>
              <option value="">All cameras</option>
              {cameras.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <select aria-label="Type" className={selectCls} value={objectClass} onChange={(e) => update({ type: e.target.value || null })}>
              <option value="">People and vehicles</option>
              <option value="person">People</option>
              <option value="vehicle">Vehicles</option>
            </select>
          </span>
        </div>
      </form>

      {status.data?.enabled === false && (
        <p role="status" className="rounded-lg border border-warn/50 bg-warn/10 px-4 py-2.5 text-sm text-warn">
          Search is switched off. An admin can turn it on in Settings → Performance (uses about 0.5–0.7 GB of extra memory).
        </p>
      )}
      {status.data?.enabled && !status.data.available && (
        <p role="status" className="rounded-lg border border-warn/50 bg-warn/10 px-4 py-2.5 text-sm text-warn">
          The search model isn't installed yet, so you can browse recent sightings but not search by description.
          Install it once with <code>uv run --group export python ../../scripts/export_siglip.py</code> in <code>services/vision</code>.
        </p>
      )}
      {results.error && <ErrorNote error={results.error} />}

      <Card title={q ? `Best matches for “${q}”` : "Recent sightings"}>
        {results.data?.length === 0 ? (
          <Empty>{q ? "Nothing matches in this period. Try a broader description or a longer time range." : "No sightings yet. They appear as cameras see people and vehicles."}</Empty>
        ) : (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {results.data?.map((s) => <SightingCard key={s.id} s={s} onOpen={() => update({ open: String(s.id) })} />)}
          </div>
        )}
      </Card>

      <Modal open={!!openId} onClose={() => update({ open: null })} wide title="Sighting">
        {openId && <SightingDialog id={Number(openId)} hours={hours} onOpen={(id) => update({ open: String(id) })} />}
      </Modal>
    </div>
  );
}
