import { setupWorker } from "msw/browser";

import { handlers } from "@/test/handlers";

// Stands in for src/test/server.ts in Storybook (main.ts swaps it), so the test fakes
// (fakeSession, fakeJobsApi, ...) register on a browser worker instead of a Node server.
export * from "@/test/handlers";
export const server = setupWorker(...handlers);

let started: Promise<unknown> | undefined;
export const startWorker = () =>
  (started ??= server.start({ onUnhandledRequest: "bypass", quiet: true }));
