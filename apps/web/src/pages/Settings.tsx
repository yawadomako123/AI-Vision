import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, type Role } from "../api";
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
  const [events, setEvents] = useState(30);
  const [counts, setCounts] = useState(90);
  useEffect(() => {
    if (settings.data) { setEvents(settings.data.event_retention_days); setCounts(settings.data.count_retention_days); }
  }, [settings.data]);
  const save = useMutation({
    mutationFn: () => api.updateSettings({ event_retention_days: events, count_retention_days: counts }),
    onSuccess: (data) => qc.setQueryData(["settings"], data),
  });
  const dirty = settings.data && (events !== settings.data.event_retention_days || counts !== settings.data.count_retention_days);

  return (
    <Card title="Data retention">
      <form className="flex flex-col gap-3 p-4 text-sm" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="text-muted">Older data is deleted automatically every hour. Snapshots are deleted with their events.</p>
        <fieldset disabled={!isAdmin} className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Keep events and snapshots (days)</span>
            <input className={inputCls} type="number" min={1} max={3650} value={events} onChange={(e) => setEvents(Number(e.target.value))} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Keep people/vehicle counts (days)</span>
            <input className={inputCls} type="number" min={1} max={3650} value={counts} onChange={(e) => setCounts(Number(e.target.value))} />
          </label>
        </fieldset>
        {save.error && <ErrorNote error={save.error} />}
        {save.isSuccess && !dirty && <p role="status" className="text-ok">Saved.</p>}
        {isAdmin && <div><Button type="submit" variant="primary" disabled={!dirty || save.isPending}>Save</Button></div>}
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
  "camera.create": "Added camera", "camera.update": "Changed camera", "camera.delete": "Deleted camera",
  "rule.create": "Added rule", "rule.update": "Changed rule", "rule.delete": "Deleted rule",
  "event.review": "Reviewed event", "events.export": "Exported events", "settings.update": "Changed settings",
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
      <div className="grid gap-5 lg:grid-cols-2">
        <Health />
        <Retention />
      </div>
      <ChangePassword />
      {isAdmin && <Users />}
      {isAdmin && <AuditLog />}
    </div>
  );
}
