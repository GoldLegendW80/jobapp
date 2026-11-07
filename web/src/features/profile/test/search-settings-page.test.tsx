import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";

// Settings, Search (owner, 2026-11-05: "search setting should be in the settings of the user").
describe("Settings, Search", () => {
  it("holds the Search tab with its save under the Settings heading, beside Applications", async () => {
    fakeSession(member);
    fakeProfile();
    const user = userEvent.setup();
    const { location } = renderApp("/settings/search");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Settings" }),
    ).toBeVisible();
    expect(document.title).toBe("Search settings · jobapp");
    const form = await screen.findByRole("form", { name: "Search" });
    expect(within(form).getByText("Job titles")).toBeVisible();
    expect(
      within(form).getByRole("button", { name: "Save search" }),
    ).toBeVisible();
    const tabs = screen.getByRole("tablist", { name: "Settings" });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Search", "Applications"]);
    expect(within(tabs).getByRole("tab", { name: "Search" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(within(tabs).getByRole("tab", { name: "Applications" }));
    await waitFor(() => {
      expect(location()).toBe("/settings/applications");
    });
    expect(
      await screen.findByRole("heading", { name: "When you click Apply" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 1, name: "Settings" }),
    ).toBeVisible();
    expect(document.title).toBe("Application settings · jobapp");
  });

  it("is what the user menu's Settings opens first", async () => {
    fakeSession(member);
    fakeProfile();
    const user = userEvent.setup();
    const { location } = renderApp("/profile?tab=identity");
    await screen.findByRole("heading", { level: 1, name: "Profile" });
    await user.click(screen.getByRole("button", { name: /^Account menu/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Settings" }));
    await waitFor(() => {
      expect(location()).toBe("/settings/search");
    });
  });
});
