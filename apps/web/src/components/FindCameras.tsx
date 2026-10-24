import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import qrcode from "qrcode-generator";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { api, type FoundDevice } from "../api";
import { Button, ErrorNote, inputCls } from "./ui";

export interface Picked { name: string; url: string }

// Add-camera wizard: search → one login for all → see each camera's picture → "Add N cameras".
// Cameras that need a setting first (e.g. Tapo's camera account) get step-by-step help instead.

type Status = "idle" | "connecting" | "ok" | "error";
interface Found { channel: number; url: string; name: string; add: boolean; mac: string | null }
interface Row {
  status: Status;
  ownLogin: boolean;   // a different login from the shared one
  user: string;
  password: string;
  message?: string;
  code?: string;
  streams: Found[];
  recorder: boolean;
}

const brandOf = (d: FoundDevice) => d.brand_name ?? (d.manufacturer || null);

function deviceTitle(d: FoundDevice): string {
  if (d.kind === "phone") return "Phone camera on this computer";
  const brand = brandOf(d);
  const kind = d.recorder ? "recorder" : "camera";
  if (brand && d.model) return `${brand} ${kind} (${d.model})`;
  if (brand) return `${brand} ${kind}`;
  return d.name || `Camera at ${d.ip}`;
}

/** Names that don't clash with existing cameras or each other: "Hikvision camera", "Hikvision camera 2"… */
function nameStreams(rows: [FoundDevice, Row][], taken: Set<string>): Map<string, string> {
  const used = new Set([...taken].map((n) => n.toLowerCase()));
  const names = new Map<string, string>();
  for (const [d, row] of rows) {
    for (const s of row.streams) {
      const base = d.kind === "phone" ? "Phone camera" : `${brandOf(d) ?? "Camera"} camera`;
      let n = row.recorder ? s.channel : 1;
      let name = n === 1 && !row.recorder ? base : `${base} ${n}`;
      while (used.has(name.toLowerCase())) name = `${base} ${++n}`;
      used.add(name.toLowerCase());
      names.set(s.url, name);
    }
  }
  return names;
}

function Thumb({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    api.streamSnapshot(url).then((u) => { made = u; if (alive) setSrc(u); else URL.revokeObjectURL(u); })
      .catch(() => alive && setFailed(true));
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [url]);
  return (
    <div className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-md bg-black/80 text-xs text-white/70">
      {src ? <img src={src} alt="" className="h-full w-full object-cover" />
        : failed ? "No picture yet" : "Loading picture…"}
    </div>
  );
}

function Help({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details className="rounded-lg border border-line px-3 py-2" open={open}>
      <summary className="cursor-pointer font-medium">{title}</summary>
      <div className="mt-2 flex flex-col gap-1.5 text-muted">{children}</div>
    </details>
  );
}

const TapoSteps = () => (
  <ol className="list-decimal pl-5">
    <li>Open the <b>Tapo</b> app on your phone and tap the camera.</li>
    <li>Tap the <b>⚙ settings</b> icon → <b>Advanced settings</b> → <b>Camera account</b>.</li>
    <li>Create a username and password. This is <i>not</i> your Tapo app login: it's only for this camera.</li>
    <li>Come back here, click <b>Search again</b>, and use that username and password.</li>
    <li className="list-none text-xs">Battery-powered Tapo cameras can't be used this way; plug-in ones can.</li>
  </ol>
);

const HikvisionSteps = ({ ip }: { ip?: string }) => (
  <ul className="list-disc pl-5">
    <li><b>Brand-new camera?</b> It must be activated once: open{" "}
      {ip ? <a className="text-accent underline" href={`http://${ip}`} target="_blank" rel="noreferrer">http://{ip}</a> : "its address"}{" "}
      in your browser and create a strong admin password. Then come back and search again.</li>
    <li>The username is usually <b>admin</b>. Use the password you set when the camera was installed.</li>
    <li><b>Cameras on a recorder (NVR/DVR)?</b> Use the recorder's login: all its cameras are found at once.</li>
    <li>After a few wrong passwords the camera locks logins for <b>30 minutes</b>. Wait, then try again.</li>
  </ul>
);

const EzvizSteps = () => (
  <ul className="list-disc pl-5">
    <li>Username <b>admin</b>; the password is the 6-letter <b>verification code</b> on the camera's sticker.</li>
    <li>Some models need local streaming switched on in the EZVIZ app first.</li>
  </ul>
);

function QrCode({ text }: { text: string }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  // Always dark on white: phone cameras read that best, whatever the dashboard theme.
  return <div className="w-56 max-w-full rounded-lg bg-white p-2" role="img" aria-label="QR code for the Larix app"
              dangerouslySetInnerHTML={{ __html: svg }} />;
}

/** A phone as a camera: scan one QR code and Larix is set up (no addresses or passwords to type). */
function PhoneCamera({ onStreaming }: { onStreaming: () => void }) {
  const setup = useQuery({ queryKey: ["phone-setup"], queryFn: api.phoneSetup, staleTime: Infinity, gcTime: 0 });
  if (setup.error) return <ErrorNote error={setup.error} />;
  if (!setup.data) return <p className="text-muted">Preparing…</p>;
  const s = setup.data;
  return (
    <div className="flex flex-wrap items-start gap-4">
      <QrCode text={s.larix_url} />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <ol className="list-decimal pl-5">
          <li>On the phone, install <b>Larix Broadcaster</b> (free, Play Store or App Store).</li>
          <li>Connect the phone to the <b>same Wi-Fi</b> as this computer.</li>
          <li>Scan this code with the phone's camera (on Android, Google Lens or any QR scanner) and allow it to open
            Larix. Tap <b>OK</b> to import the settings.</li>
          <li>In Larix, tap the round <b>record</b> button to start streaming. Keep the phone plugged in.</li>
          <li>Then click <b>My phone is streaming</b> below.</li>
        </ol>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={onStreaming}>My phone is streaming</Button>
        </div>
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Set up Larix by hand instead</summary>
          <p className="mt-1">Connections → + → URL <code className="select-all">{s.publish_url}</code>, user{" "}
            <code>{s.username}</code>, password <code className="select-all">{s.password}</code>. Video 640×480, 15 fps.</p>
        </details>
      </div>
    </div>
  );
}

function brandHelp(d: FoundDevice): ReactNode {
  if (d.brand === "tp-link") return <TapoSteps />;
  if (d.brand === "hikvision") return <HikvisionSteps ip={d.ip} />;
  if (d.brand === "ezviz") return <EzvizSteps />;
  return <p>In the camera's own app or web page, switch on <b>RTSP</b> or <b>ONVIF</b>, then search again.</p>;
}

function DeviceCard({ device, row, selected, onSelect, onRow, onRetry }: {
  device: FoundDevice; row: Row; selected: boolean;
  onSelect: (v: boolean) => void; onRow: (patch: Partial<Row>) => void; onRetry: () => void;
}) {
  const needsLogin = device.kind !== "phone";
  const done = device.already_added && row.status === "idle";
  return (
    <li className="flex flex-col gap-3 p-4">
      <div className="flex items-start gap-3">
        <input type="checkbox" className="mt-1" checked={selected} disabled={done || row.status === "ok"}
               onChange={(e) => onSelect(e.target.checked)} aria-label={`Connect ${deviceTitle(device)}`} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="font-medium">{deviceTitle(device)}</span>
          <span className="font-mono text-xs text-muted">{device.ip}</span>
        </div>
        {done && <span className="rounded bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok">Already added</span>}
        {row.status === "connecting" && <span className="text-xs text-muted">Connecting…</span>}
        {row.status === "ok" && (
          <span className="rounded bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok">
            ✓ {row.streams.length === 1 ? "Connected" : `${row.streams.length} cameras found`}
          </span>
        )}
      </div>

      {row.status === "error" && (
        <div className="flex flex-col gap-2 rounded-lg bg-bad/10 p-3 text-sm">
          <p className="text-bad">{row.message}</p>
          {row.code === "wrong_password" && (device.brand === "tp-link" || device.brand === "hikvision" || device.brand === "ezviz")
            && <Help title={`Help for ${brandOf(device)} cameras`}>{brandHelp(device)}</Help>}
        </div>
      )}

      {needsLogin && selected && row.status !== "ok" && (row.ownLogin || row.status === "error") && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">{device.brand === "tp-link" ? "Camera account username" : "Username"}</span>
            <input className={`${inputCls} w-40`} value={row.user} autoComplete="off"
                   onChange={(e) => onRow({ user: e.target.value, ownLogin: true })} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">Password</span>
            <input className={`${inputCls} w-44`} type="password" value={row.password} autoComplete="off"
                   onChange={(e) => onRow({ password: e.target.value, ownLogin: true })} />
          </label>
          <Button onClick={onRetry} disabled={row.status === "connecting"}>Try again</Button>
        </div>
      )}
      {needsLogin && selected && row.status === "idle" && !row.ownLogin && !done && (
        <button type="button" className="self-start text-xs text-accent underline" onClick={() => onRow({ ownLogin: true })}>
          This one has a different password
        </button>
      )}

      {row.status === "ok" && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          {row.streams.map((s, i) => (
            <div key={s.url} className="flex flex-col gap-1.5">
              <Thumb url={s.url} />
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={s.add}
                       onChange={(e) => onRow({ streams: row.streams.map((x, j) => (j === i ? { ...x, add: e.target.checked } : x)) })} />
                <input className={`${inputCls} min-w-0 flex-1 py-1`} value={s.name} maxLength={120} aria-label="Camera name"
                       onChange={(e) => onRow({ streams: row.streams.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
              </label>
            </div>
          ))}
        </div>
      )}
    </li>
  );
}

export function FindCameras({ onPick, onAdded }: { onPick: (p: Picked) => void; onAdded: (count: number) => void }) {
  const qc = useQueryClient();
  const search = useMutation({ mutationFn: api.discoverCameras });
  const { mutate } = search;
  useEffect(() => { mutate(); }, [mutate]);
  const existing = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });

  const devices = useMemo(() => search.data?.devices ?? [], [search.data]);
  const usable = devices.filter((d) => d.kind === "camera" || d.kind === "phone");
  const hidden = devices.filter((d) => d.kind === "hidden");
  const cloud = devices.filter((d) => d.kind === "cloud_only");

  const [rows, setRows] = useState<Record<string, Row>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [user, setUser] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // No cameras at all: the phone is the quickest way to start, so show it open.
  const phoneOpen = !!search.data && usable.length === 0 && hidden.length === 0;

  // Fresh rows for each search; everything not yet added is selected.
  useEffect(() => {
    const r: Record<string, Row> = {};
    const s: Record<string, boolean> = {};
    for (const d of devices) {
      r[d.ip] = { status: "idle", ownLogin: false, user: d.default_user, password: "", streams: [], recorder: false };
      s[d.ip] = !d.already_added;
    }
    setRows(r);
    setSelected(s);
    const first = devices.find((d) => d.kind === "camera");
    if (first) setUser(first.default_user || "admin");
  }, [devices]);

  const patch = (ip: string, p: Partial<Row>) => setRows((all) => ({ ...all, [ip]: { ...all[ip], ...p } }));

  const connectOne = async (d: FoundDevice, row: Row) => {
    patch(d.ip, { status: "connecting" });
    const login = row.ownLogin ? { username: row.user, password: row.password } : { username: user, password };
    try {
      const res = await api.connectCamera({ ip: d.ip, brand: d.brand, port: d.rtsp_ports[0] ?? 554,
                                            onvif_url: d.onvif_url, ...login });
      // Skip streams that are already cameras (e.g. a phone added earlier, or one camera of a recorder).
      const have = new Set((existing.data ?? []).map((c) => c.stream_url_masked));
      const fresh = res.ok ? res.streams.filter((s) => !have.has(s.url.replace(/\/\/([^:@/]+):[^@/]*@/, "//$1:***@"))) : [];
      if (res.ok && fresh.length === 0) {
        patch(d.ip, { status: "error", code: "already_added", message: "Everything here is already added." });
      } else if (res.ok) {
        patch(d.ip, { status: "ok", recorder: res.recorder,
                      streams: fresh.map((s) => ({ ...s, name: "", add: true, mac: d.mac })) });
      } else {
        patch(d.ip, { status: "error", code: res.code, message: res.message, user: login.username, password: login.password });
      }
    } catch (e) {
      patch(d.ip, { status: "error", code: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  // One at a time: gentle on cameras and on the network.
  const connectAll = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    for (const d of usable) {
      const row = rows[d.ip];
      if (selected[d.ip] && row && row.status !== "ok") await connectOne(d, row);
    }
    setBusy(false);
  };

  // Give connected streams names once, without overwriting what the user typed.
  useEffect(() => {
    const pending = usable.filter((d) => rows[d.ip]?.status === "ok" && rows[d.ip].streams.some((s) => !s.name));
    if (!pending.length) return;
    const taken = new Set([...(existing.data ?? []).map((c) => c.name),
                           ...Object.values(rows).flatMap((r) => r.streams.map((s) => s.name)).filter(Boolean)]);
    const names = nameStreams(pending.map((d) => [d, rows[d.ip]]), taken);
    setRows((all) => {
      const next = { ...all };
      for (const d of pending) next[d.ip] = { ...next[d.ip], streams: next[d.ip].streams.map((s) => (s.name ? s : { ...s, name: names.get(s.url) ?? "Camera" })) };
      return next;
    });
  }, [rows, usable, existing.data]);

  const toAdd = usable.flatMap((d) => (rows[d.ip]?.status === "ok" && selected[d.ip] ? rows[d.ip].streams.filter((s) => s.add && s.name.trim()) : []));
  const add = useMutation({
    mutationFn: async () => {
      for (const s of toAdd) {
        await api.createCamera({ name: s.name.trim(), stream_url: s.url, mac: s.mac });
        // Untick each one as it's added, so retrying after an error (e.g. a duplicate name) can't add it twice.
        setRows((all) => Object.fromEntries(Object.entries(all).map(([ip, r]) =>
          [ip, { ...r, streams: r.streams.map((x) => (x.url === s.url ? { ...x, add: false } : x)) }])));
      }
      return toAdd.length;
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
    onSuccess: (n) => onAdded(n),
  });

  const anyToConnect = usable.some((d) => selected[d.ip] && rows[d.ip]?.status !== "ok" && d.kind !== "phone");
  const anyPhoneToConnect = usable.some((d) => selected[d.ip] && rows[d.ip]?.status !== "ok" && d.kind === "phone");

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted" aria-live="polite">
          {search.isPending
            ? "Looking for cameras on your network (about 5 seconds)…"
            : search.data
              ? usable.length
                ? `Found ${usable.length} ${usable.length === 1 ? "camera" : "cameras"}.`
                : "No cameras found yet."
              : ""}
        </p>
        <Button onClick={() => search.mutate()} disabled={search.isPending || busy}>Search again</Button>
      </div>
      {search.error && <ErrorNote error={search.error} />}

      {usable.length > 0 && (
        <>
          {(anyToConnect || anyPhoneToConnect) && (
            <form onSubmit={connectAll} className="flex flex-col gap-2 rounded-lg border border-line bg-surface-2/40 p-3">
              {anyToConnect && (
                <>
                  <p className="font-medium">Enter your cameras' login</p>
                  <p className="text-xs text-muted">
                    It's on the camera's box or sticker, or whoever installed them set it. Most people use the same
                    one for all their cameras. Tapo cameras use the <b>camera account</b> from the Tapo app.
                  </p>
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="flex flex-col gap-1">
                      <span className="text-xs text-muted">Username</span>
                      <input className={`${inputCls} w-40`} value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />
                    </label>
                    <label className="flex flex-col gap-1">
                      <span className="text-xs text-muted">Password</span>
                      <input className={`${inputCls} w-44`} type="password" value={password} autoComplete="off"
                             onChange={(e) => setPassword(e.target.value)} autoFocus />
                    </label>
                    <Button type="submit" variant="primary" disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>
                  </div>
                </>
              )}
              {!anyToConnect && <Button type="submit" variant="primary" disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>}
            </form>
          )}

          <ul className="divide-y divide-line rounded-lg border border-line">
            {usable.map((d) => rows[d.ip] && (
              <DeviceCard key={d.ip} device={d} row={rows[d.ip]} selected={!!selected[d.ip]}
                          onSelect={(v) => setSelected((s) => ({ ...s, [d.ip]: v }))}
                          onRow={(p) => patch(d.ip, p)} onRetry={() => connectOne(d, { ...rows[d.ip], ownLogin: true })} />
            ))}
          </ul>
        </>
      )}

      {hidden.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-medium">Almost there: {hidden.length === 1 ? "this camera needs" : "these cameras need"} one setting first</p>
          {hidden.map((d) => (
            <Help key={d.ip} open title={`${brandOf(d) ?? "Camera"} ${d.recorder ? "recorder" : "camera"} at ${d.ip}`}>
              {brandHelp(d)}
            </Help>
          ))}
        </div>
      )}

      {cloud.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg border border-line p-3 text-muted">
          {cloud.map((d) => (
            <p key={d.ip}><b className="text-text">{d.brand_name} device at {d.ip}</b>: can't be used. {d.brand_name} cameras only
              send video to {d.brand_name}'s own cloud, not to other apps.</p>
          ))}
        </div>
      )}

      {search.data && (
        <Help title="Use a phone as a camera" open={phoneOpen}>
          <PhoneCamera onStreaming={() => search.mutate()} />
        </Help>
      )}

      {search.data && (
        <Help title="Don't see your camera?" open={usable.length === 0 && hidden.length === 0}>
          <p>Check it's switched on and connected to the <b>same router or Wi-Fi</b> as this computer, then click <b>Search again</b>.</p>
          <p className="font-medium text-text">Tapo</p>
          <TapoSteps />
          <p className="font-medium text-text">Hikvision</p>
          <HikvisionSteps />
          <p className="font-medium text-text">EZVIZ</p>
          <EzvizSteps />
          <p className="font-medium text-text">Other brands</p>
          <p>Switch on <b>RTSP</b> or <b>ONVIF</b> in the camera's app or web page. Ring, Nest, Blink, Arlo and most Wyze
            cameras can't be used: they only send video to their own cloud.</p>
          <p>
            Know the camera's stream address?{" "}
            <button type="button" className="text-accent underline" onClick={() => onPick({ name: "", url: "" })}>Add it by hand</button>.
          </p>
        </Help>
      )}

      {toAdd.length > 0 && (
        <div className="sticky bottom-0 -mx-1 flex items-center justify-between gap-3 border-t border-line bg-surface px-1 pt-3">
          <span className="text-muted">Untick any you don't want, and rename them if you like.</span>
          <Button variant="primary" onClick={() => add.mutate()} disabled={add.isPending}>
            {add.isPending ? "Adding…" : `Add ${toAdd.length} ${toAdd.length === 1 ? "camera" : "cameras"}`}
          </Button>
        </div>
      )}
      {add.error && <ErrorNote error={add.error} />}
    </div>
  );
}
