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
              test: /node_modules[\\/](radix-ui|@radix-ui|@floating-ui|lucide-react|sonner)[\\/]/,
            },
            {
              name: "forms",
              test: /node_modules[\\/](zod|react-hook-form|@hookform)[\\/]/,
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
