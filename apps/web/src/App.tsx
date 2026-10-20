import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router";

import { api } from "./api";
import { useAuth } from "./auth";
import { EventToasts } from "./components/Toasts";
import { timeAgo } from "./format";
import { useLive } from "./live";
import Analytics from "./pages/Analytics";
import Cameras from "./pages/Cameras";
import Events from "./pages/Events";
import Live from "./pages/Live";
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

function useUnreviewed() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.summary(24) });
  return summary.data?.unreviewed ?? 0;
}

function Badge({ n }: { n: number }) {
  if (n <= 0) return null;
  return (
    <span className="tabular rounded-full bg-accent px-1.5 text-xs text-white" aria-label={`${n} unreviewed`}>
      {n > 99 ? "99+" : n}
    </span>
  );
}

/** Phone: tab bar at the bottom (thumb reach). The less-used pages sit under More. */
function BottomNav() {
  const unreviewed = useUnreviewed();
  const { logout } = useAuth();
  const [more, setMore] = useState(false);
  const location = useLocation();
  useEffect(() => setMore(false), [location.pathname]);
  const tab = ({ isActive }: { isActive: boolean }) =>
    `relative flex flex-1 flex-col items-center gap-0.5 py-2 text-xs font-medium ${isActive ? "text-accent" : "text-muted"}`;
  const moreActive = ["/cameras", "/analytics", "/settings"].some((p) => location.pathname.startsWith(p));
  return (
    <>
      {more && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setMore(false)}>
          <nav aria-label="More" onClick={(e) => e.stopPropagation()}
               className="absolute inset-x-3 bottom-[calc(4.25rem+env(safe-area-inset-bottom))] flex flex-col rounded-xl border border-line bg-surface p-2 shadow-lg">
            {[["/cameras", "Cameras"], ["/analytics", "Analytics"], ["/settings", "Settings"]].map(([to, label]) => (
              <NavLink key={to} to={to} className="rounded-lg px-4 py-3 text-base font-medium hover:bg-surface-2">{label}</NavLink>
            ))}
            <button type="button" onClick={logout} className="rounded-lg px-4 py-3 text-left text-base text-muted hover:bg-surface-2">Sign out</button>
          </nav>
        </div>
      )}
      <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        <NavLink to="/" end className={tab}><span aria-hidden className="text-lg leading-6">⌂</span>Overview</NavLink>
        <NavLink to="/live" className={tab}><span aria-hidden className="text-lg leading-6">▦</span>Live</NavLink>
        <NavLink to="/events" className={tab}>
          <span aria-hidden className="text-lg leading-6">⚑</span>Events
          {unreviewed > 0 && <span className="absolute top-1 left-1/2 ml-2"><Badge n={unreviewed} /></span>}
        </NavLink>
        <NavLink to="/search" className={tab}><span aria-hidden className="text-lg leading-6">⌕</span>Search</NavLink>
        <button type="button" aria-expanded={more} onClick={() => setMore((v) => !v)}
                className={`flex flex-1 flex-col items-center gap-0.5 py-2 text-xs font-medium ${more || moreActive ? "text-accent" : "text-muted"}`}>
          <span aria-hidden className="text-lg leading-6">⋯</span>More
        </button>
      </nav>
    </>
  );
}

function Nav() {
  const unreviewed = useUnreviewed();
  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm font-medium ${
      isActive ? "bg-accent/12 text-accent" : "text-muted hover:bg-surface-2 hover:text-text"
    }`;
  return (
    <nav aria-label="Main" className="flex gap-1 md:flex-col">
      <NavLink to="/" end className={link}>Overview</NavLink>
      <NavLink to="/live" className={link}>Live</NavLink>
      <NavLink to="/events" className={link}>
        Events
        <Badge n={unreviewed} />
      </NavLink>
      <NavLink to="/search" className={link}>Search</NavLink>
      <NavLink to="/cameras" className={link}>Cameras</NavLink>
      <NavLink to="/analytics" className={link}>Analytics</NavLink>
      <NavLink to="/settings" className={link}>Settings</NavLink>
    </nav>
  );
}

function ArmSwitch({ compact = false }: { compact?: boolean }) {
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
      className={`flex items-center justify-between gap-2 rounded-lg border font-semibold ${compact ? "px-2.5 py-1 text-xs" : "px-3 py-2 text-sm"} ${
        armed ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/50 bg-warn/10 text-warn"
      }`}
    >
      <span className="flex items-center gap-2">
        <span aria-hidden>{armed ? "🛡️" : "⏸"}</span>
        {armed ? "Armed" : "Disarmed"}
      </span>
      {!compact && <span className="text-xs font-medium opacity-80">{armed ? "Disarm" : "Arm"}</span>}
    </button>
  );
}

function DisarmedBanner() {
  const arming = useQuery({ queryKey: ["arming"], queryFn: api.arming });
  if (!arming.data || arming.data.armed) return null;
  return (
    <p role="status" className="mb-5 rounded-lg border border-warn/50 bg-warn/10 px-4 py-2.5 text-sm text-warn">
      <strong>Disarmed</strong>: rules won't raise alerts until you arm again. Lines keep counting entries and exits,
      and camera offline alerts still work.
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
      <aside className="sticky top-0 z-20 flex flex-col gap-4 border-b border-line bg-surface px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3 md:h-dvh md:border-r md:border-b-0 md:py-5">
        <div className="flex items-center justify-between gap-2 md:flex-col md:items-start">
          <span className="flex items-center gap-2 font-semibold">
            <img src="/favicon.svg" alt="" className="size-6" />
            AI Vision Edge
          </span>
          <span className="flex items-center gap-2">
            <LiveIndicator />
            <span className="md:hidden"><ArmSwitch compact /></span>
          </span>
        </div>
        <div className="hidden md:block"><ArmSwitch /></div>
        <div className="hidden md:block"><Nav /></div>
        <div className="hidden md:mt-auto md:block"><UserMenu /></div>
      </aside>
      <BottomNav />
      <main className="mx-auto w-full max-w-7xl px-4 pt-5 pb-[calc(5.5rem+env(safe-area-inset-bottom))] md:px-8 md:py-7">
        <DisarmedBanner />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/live" element={<Live />} />
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
