import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocuments, fakePrompt } from "@/test/ingestion";
import { fakeRuns } from "@/test/ingestion-runs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";

import { ProposalList } from "./proposal-list";
import { PROMPT_READY } from "./step-prompt";
import { REVIEW_READY } from "./step-review";
import { isStepId, STEP_IDS } from "./steps";

async function open(path = "/profile/fill") {
  fakeSession(member);
  fakeDocuments();
  fakePrompt();
  fakeRuns();
  const app = renderApp(path);
  await screen.findByRole("heading", {
    level: 1,
    name: "Fill my profile with an agent",
  });
  return app;
}

const steps = () => screen.getByRole("navigation", { name: "Steps" });

describe("/profile/fill", () => {
  it("shows the three steps in order and opens on the first one that is ready", async () => {
    await open();
    expect(document.title).toBe("Fill my profile \u00b7 jobapp");
    const items = within(steps()).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("Step 1: Prepare"),
      expect.stringContaining("Step 2: Documents"),
      expect.stringContaining("Step 3: Review"),
    ]);
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Step 1: Prepare a dossier with a coding assistant",
      }),
    ).toBeVisible();
    expect(
      within(steps()).getByRole("button", { name: /Step 1: Prepare/ }),
    ).toHaveAttribute("aria-current", "step");
    expect(
      within(steps()).getByRole("button", { name: /Step 2: Documents/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("lists the three steps as controls, none of them as Soon", async () => {
    // G1, G3 and G11 shipped: the three steps can be opened.
    expect([PROMPT_READY, REVIEW_READY]).toEqual([true, true]);
    await open();
    expect(within(steps()).getAllByRole("button")).toHaveLength(3);
    for (const step of within(steps()).getAllByRole("listitem"))
      expect(step).not.toHaveTextContent("Soon");
  });

  it.each(["proposals", "nothing", ""])(
    "opens the first step for ?step=%s, a step that cannot be opened",
    async (step) => {
      await open(`/profile/fill?step=${step}`);
      expect(
        screen.getByRole("heading", { level: 2, name: /Prepare a dossier/ }),
      ).toBeVisible();
    },
  );

  it.each([
    ["prompt", "Step 1: Prepare a dossier with a coding assistant"],
    ["documents", "Step 2: Add your documents"],
    ["review", "Step 3: Review what the agent proposes"],
  ])("opens the step named by ?step=%s", async (step, heading) => {
    await open(`/profile/fill?step=${step}`);
    expect(
      screen.getByRole("heading", { level: 2, name: heading }),
    ).toBeVisible();
  });

  it("goes from the first step on to the documents", async () => {
    const user = userEvent.setup();
    const app = await open();
    await user.click(
      screen.getByRole("button", { name: "Go on to your documents" }),
    );
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Step 2: Add your documents",
      }),
    ).toBeVisible();
    expect(app.location()).toBe("/profile/fill?step=documents");
  });

  it("keeps the open step in the address", async () => {
    const user = userEvent.setup();
    const app = await open();
    await user.click(
      within(steps()).getByRole("button", { name: /Step 2: Documents/ }),
    );
    expect(app.location()).toBe("/profile/fill?step=documents");
  });

  it("says what the page is for and what it never does on its own", async () => {
    await open("/profile/fill?step=documents");
    expect(
      screen.getByText(/Nothing is written to your profile until you accept/),
    ).toBeVisible();
    expect(
      screen.getByText(/Nothing is sent to an AI model until you start a run/),
    ).toBeVisible();
  });

  it("goes back to the profile", async () => {
    const user = userEvent.setup();
    fakeProfile();
    const app = await open();
    await user.click(
      screen.getByRole("link", { name: "Back to your profile" }),
    );
    await screen.findByRole("heading", { level: 1, name: "Profile" });
    expect(app.location()).toBe("/profile");
  });

  it("is reached from the profile page", async () => {
    const user = userEvent.setup();
    fakeSession(member);
    fakeProfile();
    fakeDocuments();
    fakePrompt();
    const app = renderApp("/profile");
    await user.click(
      await screen.findByRole("link", {
        name: "Fill my profile with an agent",
      }),
    );
    await screen.findByRole("heading", {
      level: 1,
      name: "Fill my profile with an agent",
    });
    expect(app.location()).toBe("/profile/fill");
    // Still under Profile in the main navigation.
    expect(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole(
        "link",
        { name: "Profile" },
      ),
    ).toHaveAttribute("aria-current", "page");
  });

  it("asks a signed out visitor to sign in first", async () => {
    fakeSession(null);
    const app = renderApp("/profile/fill");
    await screen.findByRole("heading", { name: "Sign in" });
    expect(app.location()).toContain("/login");
  });
});

describe("the steps built by later tasks", () => {
  it("step ids are the three of the spec", () => {
    expect(STEP_IDS).toEqual(["prompt", "documents", "review"]);
    expect(isStepId("documents")).toBe(true);
    expect(isStepId("upload")).toBe(false);
    expect(isStepId(null)).toBe(false);
  });

  it("the review step lays out the start, the progress and the proposals", async () => {
    await open("/profile/fill?step=review");
    // No run yet and nothing proposed: only the start takes room.
    expect(
      await screen.findByRole("heading", { level: 3, name: "Start a run" }),
    ).toBeVisible();
    const panel = document.querySelector('[data-step="review"] > div');
    expect(panel?.childElementCount).toBe(1);
    // The list reads the last finished run, so it needs a query client.
    const withClient = (ui: ReactElement) =>
      render(
        <QueryClientProvider client={new QueryClient()}>
          {ui}
        </QueryClientProvider>,
      );
    expect(withClient(<ProposalList />).container).toBeEmptyDOMElement();
  });
});
