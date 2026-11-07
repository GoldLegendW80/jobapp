/// <reference types="vitest/config" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const root = path.dirname(fileURLToPath(import.meta.url));
// The api in dev runs on the host; inside compose.dev.yml this points at the api service.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";
const apiProxy = { "/api": { target: apiTarget, changeOrigin: true } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(root, "src"),
    },
  },
  build: {
    rolldownOptions: {
      output: {
        // Libraries in their own long-cached chunks, so a deploy of app code alone leaves them
        // in the browser cache, and no chunk passes the 500 kB warning.
        codeSplitting: {
          groups: [
            {
              name: "react",
              test: /node_modules[\\/](react|react-dom|scheduler|react-router)[\\/]/,
            },
            {
              name: "ui",
              test: /node_modules[\\/](radix-ui|@radix-ui|@floating-ui|lucide-react|sonner|clsx)[\\/]/,
            },
            {
              name: "forms",
              test: /node_modules[\\/](zod|react-hook-form|@hookform)[\\/]/,
            },
            // Discover's animations only: loaded with its lazy scene, never by another page.
            { name: "rive", test: /node_modules[\\/]@rive-app[\\/]/ },
            // The jobs filters' chart and calendar only: loaded when the Filters sheet opens.
            {
              name: "charts",
              test: /node_modules[\\/](recharts|victory-vendor|d3-[a-z-]+|internmap|decimal\.js-light|es-toolkit|eventemitter3|immer|reselect|redux|redux-thunk|@reduxjs|react-redux)[\\/]/,
            },
            {
              name: "calendar",
              test: /node_modules[\\/](react-day-picker|date-fns|@date-fns)[\\/]/,
            },
            { name: "vendor", test: /node_modules[\\/]/ },
          ],
        },
      },
    },
  },
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
