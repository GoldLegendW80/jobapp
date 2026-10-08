import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";

describe("App shell", () => {
  it("renders one banner, one main landmark and a level one heading", async () => {
    fakeSession(admin);
    renderApp("/");
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Welcome, Harald Hautin",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("banner")).toHaveTextContent("jobapp");
    expect(screen.getByRole("link", { name: "jobapp" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("shows API ok from a mocked /api/health", async () => {
    fakeSession(admin);
    renderApp("/");
    expect(await screen.findByText("API ok")).toBeInTheDocument();
  });

  it("uses no dash separators in visible text", async () => {
    fakeSession(admin);
    const { container } = renderApp("/");
    await screen.findByText("API ok");
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });
});
