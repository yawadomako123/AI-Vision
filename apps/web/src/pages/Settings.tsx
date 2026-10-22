import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, type AppSettings, type NotificationSettings, type NotificationUpdate, type Role } from "../api";
import { useAuth } from "../auth";
import { Button, Card, ErrorNote, inputCls, selectCls } from "../components/ui";
import { formatDateTime, timeAgo } from "../format";

function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function Health() {
  const sys = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 15_000 });
  const s = sys.data;
  const Row = ({ label, ok, value }: { label: string; ok?: boolean; value: string }) => (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className={`flex items-center gap-1.5 ${ok === undefined ? "" : ok ? "text-ok" : "text-bad"}`}>
        {ok !== undefined && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
        {value}
      </span>
    </div>
  );
  return (
    <Card title="System health">
      {sys.error && <div className="p-4"><ErrorNote error={sys.error} /></div>}
      {s && s.problems.length > 0 && (
        <ul className="flex flex-col gap-1 border-b border-line bg-bad/10 px-4 py-2.5 text-sm text-bad" role="alert">
          {s.problems.map((p) => <li key={p.key}><strong>{p.title}.</strong> {p.message}</li>)}
        </ul>
      )}
      {s && (
        <div className="divide-y divide-line">
          <Row label="Database" ok={s.database === "ok"} value={s.database === "ok" ? "OK" : "Error"} />
          <Row label="Vision worker" ok={s.worker.connected}
               value={s.worker.connected ? "Connected" : s.worker.last_contact ? `Last seen ${timeAgo(s.worker.last_contact)}` : "Not running"} />
          <Row label="Snapshots stored" value={`${s.storage.snapshots} (${bytes(s.storage.snapshot_bytes)})`} />
          <Row label="Disk free" ok={s.storage.disk_free_bytes > 5 * 1024 ** 3}
               value={`${bytes(s.storage.disk_free_bytes)} of ${bytes(s.storage.disk_total_bytes)}`} />
          <Row label="Events in database" value={String(s.events_total)} />
        </div>
      )}
    </Card>
  );
}

function Retention() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<AppSettings | null>(null);
  useEffect(() => { if (settings.data) setForm(settings.data); }, [settings.data]);
  const save = useMutation({
    mutationFn: () => api.updateSettings(form!),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
  if (!form) return <Card title="Data retention"><p className="p-4 text-sm text-muted">Loading…</p></Card>;
  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => setForm({ ...form, [k]: v });
  const dirty = JSON.stringify(form) !== JSON.stringify(settings.data);
  const num = (k: keyof AppSettings, label: string, min: number, max: number) => (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      <input className={inputCls} type="number" min={min} max={max} value={form[k] as number}
             onChange={(e) => set(k, Number(e.target.value) as never)} />
    </label>
  );

  return (
    <Card title="Data retention">
      <form className="flex flex-col gap-3 p-4 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="text-muted">Older data is deleted automatically every hour. Snapshots are deleted with their events.</p>
        <fieldset disabled={!isAdmin} className="grid gap-3 sm:grid-cols-2">
          {num("event_retention_days", "Keep events and snapshots (days)", 1, 3650)}
          {num("count_retention_days", "Keep people/vehicle counts (days)", 1, 3650)}
          <label className="flex items-center gap-2 sm:col-span-2">
            <input type="checkbox" checked={form.clips_enabled} onChange={(e) => set("clips_enabled", e.target.checked)} />
            Save a short clip (~8 s, low frame rate) with each alert
          </label>
          {form.clips_enabled && (
            <>
              {num("clip_retention_days", "Keep clips (days)", 1, 365)}
              {num("clip_storage_mb", "Clip storage limit (MB, oldest deleted first)", 50, 1000000)}
            </>
          )}
          {form.search_enabled && num("sighting_retention_days", "Keep search sightings (days)", 1, 365)}
        </fieldset>
        {save.error && <ErrorNote error={save.error} />}
        {save.isSuccess && !dirty && <p role="status" className="text-ok">Saved.</p>}
        {isAdmin && <div><Button type="submit" variant="primary" disabled={!dirty || save.isPending}>Save</Button></div>}
      </form>
    </Card>
  );
}

function Performance() {
  const { isAdmin } = useAuth();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (body: Partial<AppSettings>) => api.updateSettings(body),
    onSuccess: (data) => { qc.setQueryData(["settings"], data); qc.invalidateQueries({ queryKey: ["search-status"] }); },
  });
  const s = settings.data;
  if (!s) return <Card title="Performance"><p className="p-4 text-sm text-muted">Loading…</p></Card>;
  return (
    <Card title="Performance">
      <fieldset disabled={!isAdmin || save.isPending} className="flex flex-col gap-3 p-4 text-sm">
        <p className="text-muted">Changes apply to the vision worker within a few seconds, no restart needed.</p>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Analysis speed (frames checked per second, per camera)</span>
          <select className={selectCls} value={String(s.analysis_fps)} onChange={(e) => save.mutate({ analysis_fps: Number(e.target.value) })}>
            <option value="1">1: lightest, fine for slow scenes</option>
            <option value="2">2: light</option>
            <option value="3">3: balanced (recommended)</option>
            <option value="5">5: most responsive, heaviest</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Detection sensitivity</span>
          <select className={selectCls} value={s.detection_sensitivity}
                  onChange={(e) => save.mutate({ detection_sensitivity: e.target.value as AppSettings["detection_sensitivity"] })}>
            <option value="high">High: catches more, but more false or doubled boxes</option>
            <option value="balanced">Balanced (recommended)</option>
            <option value="low">Low: only very clear people and vehicles</option>
          </select>
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-0.5" checked={s.motion_skip} onChange={(e) => save.mutate({ motion_skip: e.target.checked })} />
          <span>
            Rest the AI when nothing moves
            <span className="block text-xs text-muted">Quiet cameras use a fraction of the computer's power. A quick check still runs every 2 s, and it never rests while someone is being tracked.</span>
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-0.5" checked={s.search_enabled} onChange={(e) => save.mutate({ search_enabled: e.target.checked })} />
          <span>
            Plain-English search (“person in a red jacket”)
            <span className="block text-xs text-muted">Records what the cameras see for the Search page. Uses about 0.5–0.7 GB of extra memory; leave off on weak hardware.</span>
          </span>
        </label>
        {save.error && <ErrorNote error={save.error} />}
      </fieldset>
    </Card>
  );
}

function randomTopic(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return "aivision-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function Notifications() {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["notifications"], queryFn: api.notifications });
  const [form, setForm] = useState<NotificationUpdate | null>(null);
  const [token, setToken] = useState("");
  useEffect(() => {
    if (current.data && !form) {
      const { has_token: _t, ...rest } = current.data;
      setForm(rest);
    }
  }, [current.data, form]);
  const save = useMutation({
    mutationFn: () => api.updateNotifications({ ...form, ...(token ? { token } : {}) }),
    onSuccess: (data) => { qc.setQueryData(["notifications"], data); setToken(""); },
  });
  const test = useMutation({ mutationFn: api.testNotification });
  const summary = useMutation({ mutationFn: api.sendSummary });
  if (!form) return <Card title="Phone notifications"><p className="p-4 text-sm text-muted">Loading…</p></Card>;
  const set = <K extends keyof NotificationUpdate>(k: K, v: NotificationUpdate[K]) => setForm({ ...form, [k]: v });
  const publicServer = (form.server ?? "").includes("ntfy.sh");

  return (
    <Card title="Phone notifications (ntfy)">
      <form className="flex flex-col gap-3 p-4 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <ol className="list-decimal space-y-1 pl-5 text-muted">
          <li>Install the free <strong className="text-text">ntfy</strong> app on your phone (Play Store / F-Droid / App Store).</li>
          <li>Click <strong className="text-text">Generate</strong> below for a private topic name, then <strong className="text-text">Save</strong>.</li>
          <li>In the ntfy app, tap <strong className="text-text">+</strong> and subscribe to that exact topic, then press <strong className="text-text">Send test</strong>.</li>
        </ol>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={!!form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Send alerts to my phone
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Topic (keep it secret: anyone who knows it can read the alerts)</span>
            <div className="flex gap-2">
              <input className={`${inputCls} font-mono`} value={form.topic ?? ""} onChange={(e) => set("topic", e.target.value)}
                     pattern="[\-_A-Za-z0-9]{0,64}" spellCheck={false} autoComplete="off" />
              <Button onClick={() => set("topic", randomTopic())}>Generate</Button>
            </div>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Server</span>
            <input className={inputCls} value={form.server ?? ""} onChange={(e) => set("server", e.target.value)} type="url" required />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Notify for</span>
            <select className={selectCls} value={form.min_severity} onChange={(e) => set("min_severity", e.target.value as NotificationSettings["min_severity"])}>
              <option value="high">High severity only</option>
              <option value="medium">Medium and high</option>
              <option value="low">Everything</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">At most one per rule every (seconds)</span>
            <input className={inputCls} type="number" min={0} max={86400} value={form.cooldown_seconds}
                   onChange={(e) => set("cooldown_seconds", Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Dashboard link (optional, e.g. your Tailscale URL)</span>
            <input className={inputCls} value={form.dashboard_url ?? ""} onChange={(e) => set("dashboard_url", e.target.value)}
                   placeholder="https://my-laptop.tailnet.ts.net" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Access token (only for a protected server){current.data?.has_token && " · saved"}</span>
            <input className={inputCls} type="password" value={token} onChange={(e) => setToken(e.target.value)}
                   placeholder={current.data?.has_token ? "leave empty to keep" : ""} autoComplete="off" />
          </label>
        </div>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-0.5" checked={!!form.include_snapshot} onChange={(e) => set("include_snapshot", e.target.checked)} />
          <span>
            Attach the snapshot image
            {publicServer && (
              <span className="block text-xs text-warn">
                With the public ntfy.sh server the image is uploaded to their servers. Leave this off unless you self-host ntfy.
              </span>
            )}
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={!!form.daily_summary} onChange={(e) => set("daily_summary", e.target.checked)} />
            Daily summary at
          </label>
          <input className={`${inputCls} w-28`} type="time" aria-label="Daily summary time" value={form.summary_time ?? "08:00"}
                 disabled={!form.daily_summary} onChange={(e) => set("summary_time", e.target.value)} />
          <span className="text-xs text-muted">alerts, entries/exits and camera health for the last 24 h</span>
        </div>
        {save.error && <ErrorNote error={save.error} />}
        {summary.data && (
          <p role="status" className={summary.data.ok ? "text-ok" : "text-bad"}>
            {summary.data.ok ? "Summary sent. Check your phone." : `Couldn't send: ${summary.data.error}`}
          </p>
        )}
        {test.data && (
          <p role="status" className={test.data.ok ? "text-ok" : "text-bad"}>
            {test.data.ok ? "Test sent. Check your phone." : `Test failed: ${test.data.error}`}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>Save</Button>
          <Button onClick={() => test.mutate()} disabled={test.isPending || !current.data?.topic}>
            {test.isPending ? "Sending…" : "Send test"}
          </Button>
          <Button onClick={() => summary.mutate()} disabled={summary.isPending || !current.data?.topic}>
            {summary.isPending ? "Sending…" : "Send summary now"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => { setCurrent(""); setNext(""); },
  });
  return (
    <Card title="Your password">
      <form className="flex flex-col gap-3 p-4 text-sm" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Current password</span>
          <input className={inputCls} type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required autoComplete="current-password" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">New password (10+ characters)</span>
          <input className={inputCls} type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} autoComplete="new-password" />
        </label>
        {change.error && <ErrorNote error={change.error} />}
        {change.isSuccess && <p role="status" className="text-ok">Password changed. Other devices were signed out.</p>}
        <div><Button type="submit" variant="primary" disabled={change.isPending}>Change password</Button></div>
      </form>
    </Card>
  );
}

function Users() {
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["users"] });
  const create = useMutation({
    mutationFn: () => api.createUser({ username: username.trim(), password, role }),
    onSuccess: () => { setUsername(""); setPassword(""); invalidate(); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { role?: Role; active?: boolean } }) => api.updateUser(id, body),
    onSettled: invalidate,
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };

  return (
    <Card title="Users">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr><th className="px-4 py-2 font-medium">User</th><th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Last sign-in</th><th className="px-3 py-2 font-medium">Status</th></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td className="px-4 py-2 font-medium">{u.username}{u.id === me.id && <span className="text-muted"> (you)</span>}</td>
                <td className="px-3 py-2">
                  <select aria-label={`Role for ${u.username}`} className={selectCls} value={u.role}
                          onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                    <option value="admin">Admin</option><option value="operator">Operator</option>
                  </select>
                </td>
                <td className="px-3 py-2 text-muted">{u.last_login_at ? timeAgo(u.last_login_at) : "never"}</td>
                <td className="px-3 py-2">
                  <Button onClick={() => update.mutate({ id: u.id, body: { active: !u.active } })} disabled={u.id === me.id}>
                    {u.active ? "Deactivate" : "Reactivate"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {update.error && <div className="px-4 pt-3"><ErrorNote error={update.error} /></div>}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2 border-t border-line p-4 text-sm">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">New username</span>
          <input className={inputCls} value={username} onChange={(e) => setUsername(e.target.value)} required
                 pattern="[A-Za-z0-9_.@\-]{3,60}" title="3–60 letters, numbers, or _ . @ -" autoComplete="off" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Temporary password</span>
          <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 required minLength={10} autoComplete="new-password" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Role</span>
          <select className={selectCls} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="operator">Operator</option><option value="admin">Admin</option>
          </select>
        </label>
        <Button type="submit" variant="primary" disabled={create.isPending}>Add user</Button>
        {create.error && <div className="w-full"><ErrorNote error={create.error} /></div>}
      </form>
    </Card>
  );
}

const ACTION_LABELS: Record<string, string> = {
  setup: "Created the first admin", login: "Signed in", login_failed: "Failed sign-in", logout: "Signed out",
  password_change: "Changed password", "user.create": "Added user", "user.update": "Changed user",
  "camera.create": "Added camera", "camera.update": "Changed camera", "camera.delete": "Deleted camera", "camera.discover": "Searched for cameras",
  "rule.create": "Added rule", "rule.update": "Changed rule", "rule.delete": "Deleted rule",
  "event.review": "Reviewed event", "events.export": "Exported events", "settings.update": "Changed settings",
  "settings.notifications": "Changed notifications", search: "Searched", "system.arm": "Armed the system", "system.disarm": "Disarmed the system",
};

function AuditLog() {
  const [page, setPage] = useState(0);
  const audit = useQuery({ queryKey: ["audit", page], queryFn: () => api.audit(25, page * 25) });
  const total = audit.data?.total ?? 0;
  return (
    <Card title="Audit log">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr><th className="px-4 py-2 font-medium">When</th><th className="px-3 py-2 font-medium">Who</th>
                <th className="px-3 py-2 font-medium">What</th><th className="px-3 py-2 font-medium">Details</th></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {audit.data?.items.map((a) => (
              <tr key={a.id} className={a.action === "login_failed" ? "text-bad" : ""}>
                <td className="tabular px-4 py-2 whitespace-nowrap" title={formatDateTime(a.at)}>{timeAgo(a.at)}</td>
                <td className="px-3 py-2">{a.username ?? "–"}</td>
                <td className="px-3 py-2">{ACTION_LABELS[a.action] ?? a.action}{a.target && <span className="text-muted"> · {a.target}</span>}</td>
                <td className="max-w-xs truncate px-3 py-2 text-xs text-muted" title={JSON.stringify(a.detail)}>
                  {Object.entries(a.detail).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ")}
                  {a.ip && ` (${a.ip})`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > 25 && (
        <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-sm">
          <span className="tabular text-muted">{page * 25 + 1}–{Math.min(total, (page + 1) * 25)} of {total}</span>
          <div className="flex gap-2">
            <Button disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</Button>
            <Button disabled={(page + 1) * 25 >= total} onClick={() => setPage(page + 1)}>Older</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function Settings() {
  const { isAdmin } = useAuth();
  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold">Settings</h1>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
        <Health />
        <Retention />
        <Performance />
      </div>
      {isAdmin && <Notifications />}
      <ChangePassword />
      {isAdmin && <Users />}
      {isAdmin && <AuditLog />}
    </div>
  );
}
