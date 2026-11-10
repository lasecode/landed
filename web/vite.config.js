import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api, /r and /webhooks are proxied to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "^/r/.*": "http://localhost:8000",
    },
  },
});
