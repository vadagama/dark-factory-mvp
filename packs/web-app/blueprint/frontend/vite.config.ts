import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Same topology as the console (ADR-021 p.3): the browser talks to one
    // same origin; /api is proxied to the local API (no CORS anywhere).
    // Override the target with VITE_API_TARGET, e.g. a port-forwarded cluster.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // App scope only: the @small/ui workspace package runs its own vitest
    // (ui:test) and Playwright suites from packages/ui with its own configs.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
