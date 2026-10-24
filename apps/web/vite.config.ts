import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies the API so the browser sees one origin (no CORS, WebSocket included).
// In production FastAPI serves the built files itself.
// AIV_API points it at another API, e.g. a test one while the installed service uses 8000.
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const API = env.AIV_API ?? "http://127.0.0.1:8000";

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
