import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 1420,
    strictPort: true,
    host: "0.0.0.0",
    allowedHosts: ["core.lapw1ng.com"],
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
      "/events": "http://127.0.0.1:8765",
    },
  },
});
