import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import App from "./App";
import "./index.css";
import { LiveProvider } from "./live";

const queryClient = new QueryClient({
  defaultOptions: {
    // Live updates arrive over the WebSocket; polling is only a safety net.
    queries: { staleTime: 30_000, refetchInterval: 60_000, retry: 1 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LiveProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </LiveProvider>
    </QueryClientProvider>
  </StrictMode>,
);
