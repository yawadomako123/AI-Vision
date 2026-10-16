import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router";

import { api } from "./api";
import { EventToasts } from "./components/Toasts";
import { useLive } from "./live";
import Cameras from "./pages/Cameras";
import Events from "./pages/Events";
import Overview from "./pages/Overview";

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
      <NavLink to="/cameras" className={link}>Cameras</NavLink>
    </nav>
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
        <Nav />
      </aside>
      <main className="mx-auto w-full max-w-7xl px-4 py-5 md:px-8 md:py-7">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/events" element={<Events />} />
          <Route path="/cameras" element={<Cameras />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <EventToasts />
    </div>
  );
}
