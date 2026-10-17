import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router";

import { api } from "./api";
import { useAuth } from "./auth";
import { EventToasts } from "./components/Toasts";
import { timeAgo } from "./format";
import { useLive } from "./live";
import Analytics from "./pages/Analytics";
import Cameras from "./pages/Cameras";
import Events from "./pages/Events";
import Overview from "./pages/Overview";
import RulesEditor from "./pages/RulesEditor";
import Search from "./pages/Search";
import Settings from "./pages/Settings";

function LiveIndicator() {
  const { state } = useLive();
  const live = state === "live";
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${live ? "text-ok" : "text-warn"}`} role="status">
      <span className={`size-2 rounded-full bg-current ${live ? "" : "animate-pulse"}`} aria-hidden />
      {live ? "Live" : state === "connecting" ? "Connecting…" : "Reconnecting…"}
    </span>
  );
}

function Nav() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.summary(24) });
  const unreviewed = summary.data?.unreviewed ?? 0;
  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm font-medium ${
      isActive ? "bg-accent/12 text-accent" : "text-muted hover:bg-surface-2 hover:text-text"
    }`;
  return (
    <nav aria-label="Main" className="flex gap-1 md:flex-col">
      <NavLink to="/" end className={link}>Overview</NavLink>
      <NavLink to="/events" className={link}>
        Events
        {unreviewed > 0 && (
          <span className="tabular rounded-full bg-accent px-1.5 text-xs text-white" aria-label={`${unreviewed} unreviewed`}>
            {unreviewed > 99 ? "99+" : unreviewed}
          </span>
        )}
      </NavLink>
      <NavLink to="/search" className={link}>Search</NavLink>
      <NavLink to="/cameras" className={link}>Cameras</NavLink>
      <NavLink to="/analytics" className={link}>Analytics</NavLink>
      <NavLink to="/settings" className={link}>Settings</NavLink>
    </nav>
  );
}

function ArmSwitch() {
  const qc = useQueryClient();
  const arming = useQuery({ queryKey: ["arming"], queryFn: api.arming });
  const set = useMutation({
    mutationFn: (armed: boolean) => api.setArming(armed),
    onSuccess: (data) => qc.setQueryData(["arming"], data),
  });
  if (!arming.data) return null;
  const armed = arming.data.armed;
  const who = arming.data.changed_by ? ` by ${arming.data.changed_by} ${timeAgo(arming.data.changed_at)}` : "";
  return (
    <button
      type="button"
      onClick={() => set.mutate(!armed)}
      disabled={set.isPending}
      aria-pressed={armed}
      title={`${armed ? "Armed" : "Disarmed"}${who}. Click to ${armed ? "disarm (rules stop alerting)" : "arm"}.`}
      className={`flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${
        armed ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/50 bg-warn/10 text-warn"
      }`}
    >
      <span className="flex items-center gap-2">
        <span aria-hidden>{armed ? "🛡️" : "⏸"}</span>
        {armed ? "Armed" : "Disarmed"}
      </span>
      <span className="text-xs font-medium opacity-80">{armed ? "Disarm" : "Arm"}</span>
    </button>
  );
}

function DisarmedBanner() {
  const arming = useQuery({ queryKey: ["arming"], queryFn: api.arming });
  if (!arming.data || arming.data.armed) return null;
  return (
    <p role="status" className="mb-5 rounded-lg border border-warn/50 bg-warn/10 px-4 py-2.5 text-sm text-warn">
      <strong>Disarmed</strong>: zone, tripwire, loitering and occupancy rules won't raise alerts until you arm again.
      Camera offline alerts still work.
    </p>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  return (
    <div className="flex items-center justify-between gap-2 text-sm md:mt-auto md:flex-col md:items-start md:border-t md:border-line md:pt-4">
      <span className="min-w-0 truncate">
        <span className="font-medium">{user.username}</span>
        <span className="text-muted"> · {user.role === "admin" ? "Admin" : "Operator"}</span>
      </span>
      <button type="button" onClick={logout} className="text-muted hover:text-text">Sign out</button>
    </div>
  );
}

export default function App() {
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[13rem_1fr]">
      <aside className="sticky top-0 z-10 flex flex-col gap-4 border-b border-line bg-surface px-4 py-3 md:h-dvh md:border-r md:border-b-0 md:py-5">
        <div className="flex items-center justify-between gap-2 md:flex-col md:items-start">
          <span className="flex items-center gap-2 font-semibold">
            <img src="/favicon.svg" alt="" className="size-6" />
            AI Vision Edge
          </span>
          <LiveIndicator />
        </div>
        <ArmSwitch />
        <div className="overflow-x-auto"><Nav /></div>
        <UserMenu />
      </aside>
      <main className="mx-auto w-full max-w-7xl px-4 py-5 md:px-8 md:py-7">
        <DisarmedBanner />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/events" element={<Events />} />
          <Route path="/search" element={<Search />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="/cameras/:id/rules" element={<RulesEditor />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <EventToasts />
    </div>
  );
}
