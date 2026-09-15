import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev topology mirrors production (ADR-021 p.3): the browser talks to one
    // same origin; /api is proxied to the local API (no CORS anywhere).
    // Override the target with VITE_API_TARGET, e.g. a port-forwarded cluster.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    // Playwright drives this server (npm run e2e); keep the port predictable.
    port: 4173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Playwright specs (e2e/*.spec.ts) are not vitest tests.
    exclude: ["node_modules/**", "e2e/**", "dist/**"],
  },
});
