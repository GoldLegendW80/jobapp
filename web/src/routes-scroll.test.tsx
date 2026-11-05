import { act, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "@/test/render";

/** Where the window is scrolled (jsdom lays nothing out, so the test says it). */
function scrolledTo(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
  Object.defineProperty(window, "pageYOffset", {
    value: y,
    configurable: true,
  });
}

afterEach(() => {
  scrolledTo(0);
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

// The public pages make no api call, so the router's scroll memory is all that is read here:
// the same restoration serves a posting opened from the Jobs list and Back to the list.
describe("scroll position across pages", () => {
  it("opens a new page at its top and Back returns to where the page was left", async () => {
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const { router } = renderApp("/privacy");
    await screen.findByRole("heading", { level: 1, name: "Privacy" });
    scrolledTo(1600);
    await act(async () => {
      await router.navigate("/terms");
    });
    await screen.findByRole("heading", { level: 1, name: "Terms" });
    expect(scroll).toHaveBeenLastCalledWith(0, 0);
    scrolledTo(300);
    await act(async () => {
      await router.navigate(-1);
    });
    await screen.findByRole("heading", { level: 1, name: "Privacy" });
    expect(scroll).toHaveBeenLastCalledWith(0, 1600);
  });

  it("keeps the position when only the address's query changes", async () => {
    const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const { router } = renderApp("/privacy");
    await screen.findByRole("heading", { level: 1, name: "Privacy" });
    scrolledTo(900);
    await act(async () => {
      await router.navigate("/privacy?filter=1");
    });
    expect(scroll).toHaveBeenLastCalledWith(0, 900);
  });
});
