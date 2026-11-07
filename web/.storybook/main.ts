import path from "node:path";
import { fileURLToPath } from "node:url";

import type { StorybookConfig } from "@storybook/react-vite";
import type { Plugin } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const nodeServer = path.resolve(here, "../src/test/server.ts");
const browserServer = path.resolve(here, "msw.ts");

/** Every import of src/test/server.ts, relative or aliased, gets the browser worker instead. */
const mswInTheBrowser: Plugin = {
  name: "jobapp-msw-in-the-browser",
  enforce: "pre",
  async resolveId(source, importer, options) {
    if (!source.endsWith("server") || importer === browserServer) return null;
    const resolved = await this.resolve(source, importer, {
      ...options,
      skipSelf: true,
    });
    return resolved?.id === nodeServer ? browserServer : null;
  },
};

const config: StorybookConfig = {
  stories: ["../src/**/*.mdx", "../src/**/*.stories.tsx"],
  addons: [
    "@storybook/addon-docs",
    "@storybook/addon-a11y",
    "@storybook/addon-themes",
  ],
  framework: "@storybook/react-vite",
  core: { disableTelemetry: true },
  // mockServiceWorker.js, kept out of web/public so the app's build never ships it.
  staticDirs: ["./public"],
  viteFinal: (vite) => ({
    ...vite,
    plugins: [mswInTheBrowser, ...(vite.plugins ?? [])],
  }),
};

export default config;
