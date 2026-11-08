import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BuildingAnimation } from "../components/building/building-animation";

/** A browser that can draw Rive (jsdom has no 2D canvas), whose idle callback never comes. */
function riveCanDraw() {
  vi.stubGlobal("CanvasRenderingContext2D", function Context2D() {});
  vi.stubGlobal("requestIdleCallback", () => 1);
  vi.stubGlobal("cancelIdleCallback", () => {});
}

function reducedMotion() {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

const animation = () => (
  <BuildingAnimation progress={40} finished={false} className="h-64 w-full" />
);

afterEach(() => {
  // Unmounted first: its cleanup cancels the idle callback through the stub.
  cleanup();
  vi.unstubAllGlobals();
});

describe("BuildingAnimation", () => {
  it("keeps its box, hidden from screen readers, while the scene is coming", () => {
    riveCanDraw();
    const { container } = render(animation());
    const box = container.firstElementChild;
    expect(box).toHaveAttribute("aria-hidden", "true");
    expect(box).toHaveClass("h-64");
  });

  it("takes no space with reduced motion", () => {
    riveCanDraw();
    reducedMotion();
    const { container } = render(animation());
    expect(container).toBeEmptyDOMElement();
  });

  it("takes no space where Rive cannot draw (no canvas here)", () => {
    const { container } = render(animation());
    expect(container).toBeEmptyDOMElement();
  });
});
