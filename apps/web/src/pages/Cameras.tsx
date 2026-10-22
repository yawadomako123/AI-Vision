import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, type Camera, type CameraTestResult } from "../api";
import { useAuth } from "../auth";
import { CameraStatusBadge } from "../components/Badges";
import { FindCameras, type Picked } from "../components/FindCameras";
import { Modal } from "../components/Modal";
import { Button, Card, Empty, ErrorNote, inputCls } from "../components/ui";
import { liveViewUrl, timeAgo } from "../format";

function CameraForm({ camera, picked, onDone }: { camera?: Camera; picked?: Picked; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(camera?.name ?? picked?.name ?? "");
  const [location, setLocation] = useState(camera?.location ?? "");
  const [url, setUrl] = useState(picked?.url ?? "");
  const probe = useMutation({ mutationFn: () => api.probeStream(url.trim()) });

  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), location: location.trim() || null, ...(url.trim() ? { stream_url: url.trim() } : {}) };
      return camera ? api.updateCamera(camera.id, body) : api.createCamera({ ...body, stream_url: url.trim() });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      onDone();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-muted">Name</span>
        <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} placeholder="Front door" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-muted">Location (optional)</span>
        <input className={inputCls} value={location} onChange={(e) => setLocation(e.target.value)} maxLength={200} placeholder="Ground floor" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-muted">Stream URL {camera && "(leave empty to keep the current one)"}</span>
        <input className={`${inputCls} font-mono`} value={url} onChange={(e) => setUrl(e.target.value)}
               required={!camera} placeholder="rtsp://user:password@192.168.1.50:554/stream2" autoComplete="off" spellCheck={false} />
        <span className="text-xs text-muted">
          Phone via Larix: <code>rtsp://127.0.0.1:8554/cam1</code>. Passwords are encrypted and never shown again.
        </span>
      </label>
      {probe.data && (
        <p role="status" className={probe.data.ok ? "text-ok" : "text-bad"}>
          {probe.data.ok ? `Stream OK: ${probe.data.width}×${probe.data.height}, ${probe.data.codec}` : `Couldn't open the stream: ${probe.data.error}`}
        </p>
      )}
      {save.error && <ErrorNote error={save.error} />}
      <div className="flex justify-end gap-2 pt-1">
        {url.trim() && (
          <Button className="mr-auto" onClick={() => probe.mutate()} disabled={probe.isPending}>
            {probe.isPending ? "Checking…" : "Check stream"}
          </Button>
        )}
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>{camera ? "Save" : "Add camera"}</Button>
      </div>
    </form>
  );
}

function CameraCard({ camera, onEdit }: { camera: Camera; onEdit: () => void }) {
  const qc = useQueryClient();
  const { isAdmin } = useAuth();
  const [showLive, setShowLive] = useState(false);
  const [test, setTest] = useState<CameraTestResult | null>(null);
  const live = liveViewUrl(camera.stream_url_masked, camera.live_path);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["cameras"] });
  const toggle = useMutation({ mutationFn: () => api.updateCamera(camera.id, { enabled: !camera.enabled }), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.deleteCamera(camera.id), onSuccess: invalidate });
  const runTest = useMutation({ mutationFn: () => api.testCamera(camera.id), onSuccess: setTest });
  const mute = useMutation({ mutationFn: (minutes: number) => api.muteCamera(camera.id, minutes), onSuccess: invalidate });
  const mutedUntil = camera.notify_muted_until && new Date(camera.notify_muted_until) > new Date()
    ? new Date(camera.notify_muted_until) : null;

  return (
    <Card>
      {showLive && live && (
        <iframe src={live} title={`Live view: ${camera.name}`} allow="autoplay; fullscreen"
                className="aspect-video w-full rounded-t-xl border-b border-line bg-black" />
      )}
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate font-semibold">{camera.name}</h2>
            {camera.location && <p className="text-sm text-muted">{camera.location}</p>}
          </div>
          <CameraStatusBadge status={camera.status} enabled={camera.enabled} />
        </div>

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-muted">Stream</dt>
          <dd className="truncate font-mono text-xs leading-5" title={camera.stream_url_masked}>{camera.stream_url_masked}</dd>
          <dt className="text-muted">Analysing</dt>
          <dd className="tabular">{camera.status === "online" && camera.infer_fps != null ? `${camera.infer_fps.toFixed(1)} frames/s` : "–"}</dd>
          <dt className="text-muted">Last frame</dt>
          <dd>{timeAgo(camera.last_seen_at)}</dd>
        </dl>

        {mutedUntil && (
          <p className="flex items-center justify-between gap-2 rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">
            <span>Phone notifications muted until {mutedUntil.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. Detection still runs.</span>
            <button type="button" className="font-medium underline" onClick={() => mute.mutate(0)} disabled={mute.isPending}>Unmute</button>
          </p>
        )}
        {test && (
          <p role="status" className={`rounded-lg px-3 py-2 text-sm ${test.ok ? "bg-ok/10 text-ok" : "bg-bad/10 text-bad"}`}>
            {test.ok ? `Connected: ${test.width}×${test.height} ${test.codec}` : `Could not connect: ${test.error}`}
          </p>
        )}
        {(toggle.error || remove.error) && <ErrorNote error={toggle.error || remove.error} />}

        <div className="flex flex-wrap gap-2">
          {live && <Button onClick={() => setShowLive((v) => !v)}>{showLive ? "Hide live" : "Live view"}</Button>}
          <Link to={`/cameras/${camera.id}/rules`}
                className="inline-flex items-center rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2">
            Rules
          </Link>
          {camera.live_path && (
            <Link to={`/playback?camera=${camera.id}`}
                  className="inline-flex items-center rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2">
              Recordings
            </Link>
          )}
          <Button onClick={() => runTest.mutate()} disabled={runTest.isPending}>{runTest.isPending ? "Testing…" : "Test"}</Button>
          {!mutedUntil && (
            <Button onClick={() => mute.mutate(60)} disabled={mute.isPending} title="Silence phone notifications from this camera for 1 hour">
              Mute 1 h
            </Button>
          )}
          {isAdmin && (
            <>
              <Button onClick={onEdit}>Edit</Button>
              <Button onClick={() => toggle.mutate()} disabled={toggle.isPending}>{camera.enabled ? "Disable" : "Enable"}</Button>
              <Button variant="danger" disabled={remove.isPending}
                      onClick={() => confirm(`Delete "${camera.name}" and all of its events?`) && remove.mutate()}>
                Delete
              </Button>
            </>
          )}
        </div>
      </div>
    </Card>
  );
}

export default function Cameras() {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const { isAdmin } = useAuth();
  const [editing, setEditing] = useState<Camera | "new" | null>(null);
  const [finding, setFinding] = useState(false);
  const [picked, setPicked] = useState<Picked | undefined>();

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Cameras</h1>
        {isAdmin && (
          <div className="flex gap-2">
            <Button onClick={() => setFinding(true)}>Find cameras</Button>
            <Button variant="primary" onClick={() => { setPicked(undefined); setEditing("new"); }}>Add camera</Button>
          </div>
        )}
      </div>
      {cameras.error && <ErrorNote error={cameras.error} />}
      {cameras.data?.length === 0 && (
        <Card><Empty>No cameras yet. Add your phone (Larix) or an IP camera to start.</Empty></Card>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cameras.data?.map((c) => <CameraCard key={c.id} camera={c} onEdit={() => setEditing(c)} />)}
      </div>

      <Modal open={editing !== null} onClose={() => setEditing(null)}
             title={editing === "new" ? "Add camera" : `Edit ${editing?.name ?? ""}`}>
        {editing !== null && (
          <CameraForm key={editing === "new" ? `new-${picked?.url ?? ""}` : editing.id} camera={editing === "new" ? undefined : editing}
                      picked={editing === "new" ? picked : undefined} onDone={() => setEditing(null)} />
        )}
      </Modal>
      <Modal open={finding} onClose={() => setFinding(false)} wide title="Find cameras on your network">
        {finding && <FindCameras onPick={(p) => { setPicked(p); setFinding(false); setEditing("new"); }} />}
      </Modal>
    </div>
  );
}
