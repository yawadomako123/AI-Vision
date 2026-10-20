import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { api, type Camera } from "../api";
import { CameraStatusBadge } from "../components/Badges";
import { Button, Card, Empty } from "../components/ui";
import { eventLabel, liveViewUrl } from "../format";
import { useLive, type Detections } from "../live";

const LAYOUTS = { 1: "grid-cols-1", 4: "grid-cols-1 sm:grid-cols-2", 9: "grid-cols-2 lg:grid-cols-3" } as const;
type Layout = keyof typeof LAYOUTS;
const BOX_COLOR: Record<string, string> = { person: "#38bdf8" };
const STALE_MS = 2500; // hide boxes this long after the last update (camera stalled, worker gone)

function readPref(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function writePref(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* private mode: not remembered */ }
}

/** Plays a MediaMTX path over WebRTC (WHEP). Returns false from onFail so the tile can fall back. */
function WebRtcVideo({ url, onState }: { url: string; onState: (ok: boolean) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    let pc: RTCPeerConnection | null = null;
    let retry: number | undefined;
    let closed = false;
    const start = async () => {
      pc?.close();
      pc = new RTCPeerConnection();
      const conn = pc;
      conn.addTransceiver("video", { direction: "recvonly" });
      conn.ontrack = (e) => {
        if (video.current) video.current.srcObject = e.streams[0] ?? new MediaStream([e.track]);
      };
      conn.onconnectionstatechange = () => {
        if (conn.connectionState === "connected") onState(true);
        if (["failed", "disconnected", "closed"].includes(conn.connectionState) && !closed) {
          onState(false);
          retry = window.setTimeout(start, 3000);
        }
      };
      try {
        await conn.setLocalDescription(await conn.createOffer());
        await new Promise<void>((done) => {  // gather ICE candidates up front (no trickle)
          if (conn.iceGatheringState === "complete") return done();
          const t = window.setTimeout(done, 1500);
          conn.onicegatheringstatechange = () => {
            if (conn.iceGatheringState === "complete") { window.clearTimeout(t); done(); }
          };
        });
        const r = await fetch(`${url}/whep`, {
          method: "POST", headers: { "Content-Type": "application/sdp" }, body: conn.localDescription!.sdp,
        });
        if (!r.ok) throw new Error(`WHEP ${r.status}`);
        await conn.setRemoteDescription({ type: "answer", sdp: await r.text() });
      } catch {
        if (!closed) { onState(false); retry = window.setTimeout(start, 5000); }
      }
    };
    start();
    return () => { closed = true; window.clearTimeout(retry); pc?.close(); };
  }, [url, onState]);
  return <video ref={video} autoPlay muted playsInline className="absolute inset-0 size-full object-fill" />;
}

function Tile({ camera, detections, alert, showBoxes, single }: {
  camera: Camera; detections: Detections | undefined; alert: string | null; showBoxes: boolean; single: boolean;
}) {
  const tile = useRef<HTMLDivElement>(null);
  const webrtc = liveViewUrl(camera.stream_url_masked);
  const [videoOk, setVideoOk] = useState(false);
  const [previewSeq, setPreviewSeq] = useState(0);
  const [, tick] = useState(0);
  useEffect(() => { if (detections?.preview) setPreviewSeq((n) => n + 1); }, [detections]);
  useEffect(() => { const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t); }, []);

  const fresh = detections && Date.now() - detections.at * 1000 < STALE_MS + 1500;
  const aspect = detections ? `${detections.width} / ${detections.height}` : "16 / 9";
  const usePreview = !webrtc || !videoOk;
  const people = detections?.objects.filter((o) => o.cls === "person").length ?? 0;
  const vehicles = (detections?.objects.length ?? 0) - people;

  return (
    <div ref={tile}
         className={`group relative flex flex-col overflow-hidden rounded-xl border bg-black transition-shadow ${alert ? "border-bad ring-4 ring-bad/60" : "border-line"}`}>
      <div className="relative mx-auto w-full" style={{ aspectRatio: aspect, maxHeight: single ? "75vh" : undefined }}>
        {webrtc && <WebRtcVideo url={webrtc} onState={setVideoOk} />}
        {usePreview && previewSeq > 0 && (
          <img src={`/api/cameras/${camera.id}/preview?n=${previewSeq}`} alt="" className="absolute inset-0 size-full object-fill" />
        )}
        {usePreview && previewSeq === 0 && (
          <p className="absolute inset-0 grid place-items-center p-4 text-center text-sm text-white/60">
            {camera.status === "online" ? "Waiting for the first picture…" : "Camera is offline"}
          </p>
        )}
        {showBoxes && fresh && (
          <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 size-full">
            {detections!.objects.map((o, i) => (
              <rect key={`${o.track ?? "n"}-${i}`} x={o.box[0]} y={o.box[1]} width={o.box[2] - o.box[0]} height={o.box[3] - o.box[1]}
                    fill="none" stroke={BOX_COLOR[o.cls] ?? "#fbbf24"} strokeWidth={2} vectorEffect="non-scaling-stroke" />
            ))}
          </svg>
        )}
        {showBoxes && fresh && detections!.objects.map((o, i) => (
          <span key={`l-${o.track ?? "n"}-${i}`}
                className="pointer-events-none absolute -translate-y-full px-1 text-[10px] leading-4 font-medium whitespace-nowrap text-black"
                style={{ left: `${o.box[0] * 100}%`, top: `${o.box[1] * 100}%`, background: BOX_COLOR[o.cls] ?? "#fbbf24" }}>
            {o.track !== null && `#${o.track} `}{o.cls}
          </span>
        ))}
        {alert && (
          <span role="status" className="absolute top-2 left-1/2 -translate-x-1/2 rounded-md bg-bad px-2 py-1 text-xs font-semibold text-white shadow">
            {alert}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 bg-surface px-3 py-2 text-sm">
        <span className="min-w-0 flex-1 truncate font-medium">{camera.name}</span>
        {fresh && <span className="tabular text-xs text-muted">{people} {people === 1 ? "person" : "people"}{vehicles > 0 && ` · ${vehicles} vehicle${vehicles === 1 ? "" : "s"}`}</span>}
        <CameraStatusBadge status={camera.status} enabled={camera.enabled} />
        <button type="button" aria-label={`Full screen: ${camera.name}`} title="Full screen"
                onClick={() => (document.fullscreenElement ? document.exitFullscreen() : tile.current?.requestFullscreen())}
                className="rounded px-1.5 py-0.5 text-muted hover:bg-surface-2 hover:text-text">⛶</button>
      </div>
    </div>
  );
}

export default function Live() {
  const { watch, onDetections, onEvent, state } = useLive();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [params, setParams] = useSearchParams();
  const layout = (Number(params.get("layout") ?? readPref("live.layout", "4")) as Layout) in LAYOUTS
    ? (Number(params.get("layout") ?? readPref("live.layout", "4")) as Layout) : 4;
  const page = Math.max(0, Number(params.get("page") ?? 0));
  const [showBoxes, setShowBoxes] = useState(() => readPref("live.boxes", "1") === "1");
  const [detections, setDetections] = useState<Record<number, Detections>>({});
  const [alerts, setAlerts] = useState<Record<number, string>>({});
  const wall = useRef<HTMLDivElement>(null);

  const enabled = (cameras.data ?? []).filter((c) => c.enabled);
  const pages = Math.max(1, Math.ceil(enabled.length / layout));
  const shown = enabled.slice(page * layout, page * layout + layout);
  const shownIds = shown.map((c) => c.id).join(",");

  useEffect(() => {
    watch(shownIds ? shownIds.split(",").map(Number) : []);
    return () => watch([]);
  }, [shownIds, watch]);
  useEffect(() => onDetections((d) => setDetections((prev) => ({ ...prev, [d.camera_id]: d }))), [onDetections]);
  useEffect(() => onEvent((e) => {
    setAlerts((prev) => ({ ...prev, [e.camera_id]: eventLabel(e.event_type) }));
    window.setTimeout(() => setAlerts((prev) => {
      const next = { ...prev };
      delete next[e.camera_id];
      return next;
    }), 6000);
  }), [onEvent]);

  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([k, v]) => next.set(k, v));
    setParams(next, { replace: true });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Live</h1>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Layout" className="flex overflow-hidden rounded-lg border border-line">
            {([1, 4, 9] as Layout[]).map((n) => (
              <button key={n} type="button" aria-pressed={layout === n}
                      onClick={() => { writePref("live.layout", String(n)); set({ layout: String(n), page: "0" }); }}
                      className={`px-3 py-1.5 text-sm font-medium ${layout === n ? "bg-accent text-white" : "bg-surface hover:bg-surface-2"}`}>
                {n === 1 ? "1" : n === 4 ? "2×2" : "3×3"}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={showBoxes}
                   onChange={(e) => { setShowBoxes(e.target.checked); writePref("live.boxes", e.target.checked ? "1" : "0"); }} />
            Show detections
          </label>
          {pages > 1 && (
            <span className="flex items-center gap-1">
              <Button disabled={page === 0} onClick={() => set({ page: String(page - 1) })}>‹</Button>
              <span className="tabular text-sm text-muted">{page + 1}/{pages}</span>
              <Button disabled={page >= pages - 1} onClick={() => set({ page: String(page + 1) })}>›</Button>
            </span>
          )}
          <Button onClick={() => (document.fullscreenElement ? document.exitFullscreen() : wall.current?.requestFullscreen())}>
            Full screen
          </Button>
        </div>
      </div>
      {state !== "live" && <p className="text-sm text-warn">Reconnecting to live updates…</p>}
      {cameras.data && enabled.length === 0 && <Card><Empty>No cameras yet. Add one in Cameras.</Empty></Card>}
      <div ref={wall} className={`grid gap-3 bg-bg ${LAYOUTS[layout]}`}>
        {shown.map((c) => (
          <Tile key={c.id} camera={c} detections={detections[c.id]} alert={alerts[c.id] ?? null}
                showBoxes={showBoxes} single={layout === 1} />
        ))}
      </div>
      <p className="text-xs text-muted">
        Boxes come from the vision worker (a few times a second) and can trail fast movement slightly. Cameras that go
        through this computer's MediaMTX play as smooth video; others show a picture refreshed every second.
      </p>
    </div>
  );
}
