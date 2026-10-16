import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies the API so the browser sees one origin (no CORS, WebSocket included).
// In production FastAPI serves the built files itself.
const API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, ws: true },
      "/media": API,
    },
  },
});
