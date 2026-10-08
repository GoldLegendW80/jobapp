import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithQuery } from "@/test/render";

describe("App", () => {
  it("renders the shell with one main landmark and a level one heading", () => {
    renderWithQuery(<App />);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "jobapp" }),
    ).toBeInTheDocument();
  });

  it("shows API ok from a mocked /api/health", async () => {
    renderWithQuery(<App />);
    expect(await screen.findByText("API ok")).toBeInTheDocument();
  });

  it("uses no dash separators in visible text", async () => {
    const { container } = renderWithQuery(<App />);
    await screen.findByText("API ok");
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });
});
