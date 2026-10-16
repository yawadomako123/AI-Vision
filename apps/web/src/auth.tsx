import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api, UNAUTHORIZED_EVENT, type User } from "./api";
import Login from "./pages/Login";

interface AuthValue {
  user: User;
  isAdmin: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/** Shows first-run setup or login until there is a session, then renders the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: api.authStatus, refetchInterval: false, staleTime: Infinity });

  useEffect(() => {
    const onUnauthorized = () => qc.invalidateQueries({ queryKey: ["auth"] });
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  if (status.isPending) {
    return <div className="grid min-h-dvh place-items-center text-sm text-muted">Loading…</div>;
  }
  if (status.error) {
    return (
      <div className="grid min-h-dvh place-items-center p-4 text-center text-sm">
        <div>
          <p className="font-medium">Can't reach the AI Vision Edge API.</p>
          <p className="mt-1 text-muted">Is it running? ({status.error.message})</p>
          <button className="mt-3 text-accent" onClick={() => status.refetch()}>Try again</button>
        </div>
      </div>
    );
  }
  const { setup_required, user } = status.data;
  if (setup_required || !user) {
    return <Login mode={setup_required ? "setup" : "login"} onDone={() => qc.resetQueries()} />;
  }

  const value: AuthValue = {
    user,
    isAdmin: user.role === "admin",
    logout: async () => {
      await api.logout().catch(() => {});
      // Drop every cached page of data, then show the login screen.
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      qc.setQueryData(["auth"], { setup_required: false, user: null });
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthGate");
  return ctx;
}
