import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

describe("App shell", () => {
  it("renders one banner, one main landmark and a level one heading", async () => {
    fakeSession(admin);
    renderApp("/");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("banner")).toHaveTextContent("jobapp");
    expect(screen.getByRole("link", { name: "jobapp" })).toHaveAttribute(
      "href",
      "/jobs",
    );
  });

  it("sends / to the jobs dashboard, the home page", async () => {
    fakeSession(member);
    const { location } = renderApp("/");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(location()).toBe("/jobs");
  });

  it("lists Jobs first in the main navigation and marks it current", async () => {
    fakeSession(admin);
    renderApp("/jobs");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    const links = within(
      screen.getByRole("navigation", { name: "Main" }),
    ).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Jobs",
      "Profile",
      "Applications",
      "Runs",
      "Admin",
    ]);
    expect(links[0]).toHaveAttribute("aria-current", "page");
  });

  it("shows API ok from a mocked /api/health in the footer", async () => {
    fakeSession(admin);
    renderApp("/");
    const footer = await screen.findByRole("contentinfo");
    expect(await within(footer).findByText("API ok")).toBeInTheDocument();
  });

  it("uses no dash separators in visible text", async () => {
    fakeSession(admin);
    const { container } = renderApp("/");
    await screen.findByText("API ok");
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });
});
