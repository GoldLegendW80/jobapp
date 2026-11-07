/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeckScene } from "../components/deck/deck-scene";

const web = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);
const manifest = (file: string) =>
  JSON.parse(readFileSync(path.join(web, file), "utf8")) as {
    version?: string;
    dependencies?: Record<string, string>;
  };

describe("DeckScene", () => {
  it("keeps its fallback where Rive cannot draw (no canvas here), hidden from screen readers", () => {
    render(
      <DeckScene scene="Done" motion fallback={<span>Still icon</span>} />,
    );
    const scene = screen.getByText("Still icon").closest("[data-scene]");
    expect(scene).toHaveAttribute("data-state", "still");
    expect(scene).toHaveAttribute("aria-hidden", "true");
    expect(scene?.querySelector("canvas")).toBeNull();
  });

  it("never loads with reduced motion", () => {
    render(
      <DeckScene
        scene="Reaction"
        motion={false}
        fallback={<span>Icon</span>}
      />,
    );
    expect(screen.getByText("Icon").closest("[data-scene]")).toHaveAttribute(
      "data-state",
      "still",
    );
  });
});

describe("the Rive runtime", () => {
  it("serves the wasm of the very runtime react-canvas runs (one version of @rive-app/canvas)", () => {
    const app = manifest("package.json");
    const react = manifest("node_modules/@rive-app/react-canvas/package.json");
    expect(app.dependencies?.["@rive-app/canvas"]).toBe(
      react.dependencies?.["@rive-app/canvas"],
    );
    expect(manifest("node_modules/@rive-app/canvas/package.json").version).toBe(
      app.dependencies?.["@rive-app/canvas"],
    );
  });

  it("ships the built scenes, with each artboard's state machine", () => {
    const riv = readFileSync(
      path.join(web, "src/features/opportunities/assets/discover.riv"),
    ).toString("latin1");
    expect(riv.startsWith("RIVE")).toBe(true);
    for (const name of ["Reaction", "Hint", "Done", "Empty", "Loading"])
      expect(riv).toContain(name);
  });
});
