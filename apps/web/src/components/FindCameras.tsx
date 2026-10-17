import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type FoundDevice } from "../api";
import { Button, ErrorNote, inputCls } from "./ui";

export interface Picked { name: string; url: string }

function deviceTitle(d: FoundDevice): string {
  if (d.name) return d.name;
  if (d.manufacturer) return `${d.manufacturer} ${d.model ?? ""}`.trim();
  return d.brand ? `${d.brand} camera` : `Device at ${d.ip}`;
}

function DeviceRow({ device, onPick }: { device: FoundDevice; onPick: (p: Picked) => void }) {
  const [user, setUser] = useState("admin");
  const [password, setPassword] = useState("");
  const name = deviceTitle(device);
  const streams = useMutation({ mutationFn: () => api.onvifStreams(device.onvif_url!, user, password) });
  // Brand templates need the camera's login filled in (encoded: passwords may contain @ or :).
  const fill = (url: string) =>
    url.replace("USER:PASSWORD@", user || password ? `${encodeURIComponent(user)}:${encodeURIComponent(password)}@` : "");
  const needsLogin = !!device.onvif_url || device.suggestions.some((s) => s.url.includes("USER:PASSWORD@"));
  const port = device.rtsp_ports[0];

  return (
    <li className="flex flex-col gap-2.5 p-4">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium">{name}</span>
        <span className="font-mono text-xs text-muted">
          {device.ip}{device.rtsp_ports.length > 0 && ` · RTSP ${device.rtsp_ports.join(", ")}`}
        </span>
        {device.onvif_url && <span className="rounded bg-accent/15 px-1.5 text-xs font-medium text-accent">ONVIF</span>}
        {device.already_added && <span className="rounded bg-ok/15 px-1.5 text-xs font-medium text-ok">Added</span>}
      </div>
      {needsLogin && (
        <form className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => { e.preventDefault(); if (device.onvif_url) streams.mutate(); }}>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">Camera username</span>
            <input className={`${inputCls} w-36`} value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">Camera password</span>
            <input className={`${inputCls} w-44`} type="password" value={password}
                   onChange={(e) => setPassword(e.target.value)} autoComplete="off" />
          </label>
          {device.onvif_url && (
            <Button type="submit" variant="primary" disabled={streams.isPending}>
              {streams.isPending ? "Asking camera…" : "Get streams"}
            </Button>
          )}
        </form>
      )}
      {streams.error && <ErrorNote error={streams.error} />}
      <div className="flex flex-wrap gap-2">
        {streams.data?.streams.map((s, i) => (
          <Button key={s.url} variant={i === 0 ? "primary" : "secondary"} onClick={() => onPick({ name, url: s.url })}
                  title={i === 0 ? "Lowest resolution: lightest to analyse" : undefined}>
            Use {s.name}{s.width > 0 && ` (${s.width}×${s.height})`}{i === 0 && " · recommended"}
          </Button>
        ))}
        {!streams.data && device.suggestions.map((s) => (
          <Button key={s.url} onClick={() => onPick({ name, url: fill(s.url) })}>Use {s.label.toLowerCase()}</Button>
        ))}
        {!streams.data && !device.onvif_url && device.suggestions.length === 0 && (
          <Button onClick={() => onPick({ name, url: `rtsp://${device.ip}${port && port !== 554 ? `:${port}` : ""}/` })}>
            Use (then enter the stream path)
          </Button>
        )}
      </div>
    </li>
  );
}

export function FindCameras({ onPick }: { onPick: (p: Picked) => void }) {
  const search = useMutation({ mutationFn: api.discoverCameras });
  const { mutate } = search;
  useEffect(() => { mutate(); }, [mutate]);
  const devices = search.data?.devices ?? [];

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted" aria-live="polite">
          {search.isPending
            ? "Searching your network (about 5 seconds)…"
            : search.data
              ? `Found ${devices.length} ${devices.length === 1 ? "device" : "devices"} on ${search.data.subnets.join(", ") || "no network"}.`
              : ""}
        </p>
        <Button onClick={() => search.mutate()} disabled={search.isPending}>Search again</Button>
      </div>
      {search.error && <ErrorNote error={search.error} />}
      {devices.length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {devices.map((d) => <DeviceRow key={d.ip} device={d} onPick={onPick} />)}
        </ul>
      )}
      {search.data && (
        <p className="text-xs text-muted">
          Cameras must be on the same network as this computer. Not listed? Check it's powered and connected, and that
          ONVIF or RTSP is switched on in the camera's own app or web page. You can always add it by hand with its RTSP address.
        </p>
      )}
    </div>
  );
}
