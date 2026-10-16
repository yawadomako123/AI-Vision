import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { ApiError } from "./api";
import App from "./App";
import { AuthGate } from "./auth";
import "./index.css";
import { LiveProvider } from "./live";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Live updates arrive over the WebSocket; polling is only a safety net.
      staleTime: 30_000,
      refetchInterval: 60_000,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate>
          <LiveProvider>
            <App />
          </LiveProvider>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
