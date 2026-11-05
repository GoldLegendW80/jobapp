import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

// jsdom lacks a few browser APIs that Radix menus and Sonner use.
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  });
}
const missing = (name: string, target: object) => !(name in target);
if (missing("ResizeObserver", globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}
// jsdom lays nothing out and only warns on window.scrollTo, which React Router's
// ScrollRestoration calls on every navigation: a no op (tests spy on it where it matters).
Object.defineProperty(window, "scrollTo", {
  writable: true,
  configurable: true,
  value: () => {},
});
const proto = Element.prototype as unknown as Record<string, unknown>;
for (const [name, fn] of [
  ["hasPointerCapture", () => false],
  ["releasePointerCapture", () => {}],
  ["setPointerCapture", () => {}],
  ["scrollIntoView", () => {}],
] as const) {
  if (missing(name, proto)) proto[name] = fn;
}

// Any request without a handler fails the test, so nothing reaches a real network.
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => {
  server.close();
});
