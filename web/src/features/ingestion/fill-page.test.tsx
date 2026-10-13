import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocuments, fakePrompt } from "@/test/ingestion";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { ProposalList } from "./proposal-list";
import { RunProgress } from "./run-progress";
import { RunStart } from "./run-start";
import { PROMPT_READY } from "./step-prompt";
import { REVIEW_READY, StepReview } from "./step-review";
import { isStepId, STEP_IDS } from "./steps";

async function open(path = "/profile/fill") {
  fakeSession(member);
  fakeDocuments();
  fakePrompt();
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

  it("lists a step that is not built yet as Soon, and it is not a control", async () => {
    // What G1 and G3 ship: the review belongs to later tasks.
    expect([PROMPT_READY, REVIEW_READY]).toEqual([true, false]);
    await open();
    expect(within(steps()).getAllByRole("button")).toHaveLength(2);
    const [prepare, documents, review] =
      within(steps()).getAllByRole("listitem");
    expect(prepare).not.toHaveTextContent("Soon");
    expect(documents).not.toHaveTextContent("Soon");
    expect(review).toHaveTextContent("Soon");
    expect(within(review as HTMLElement).queryByRole("button")).toBeNull();
  });

  it.each(["review", "nothing", ""])(
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

  it("the review step lays out the start, the progress and the proposals", () => {
    // The list of proposals asks which run finished last: none has.
    server.use(
      http.get("*/api/ingestion/runs", () =>
        HttpResponse.json({ runs: [], active_run_id: null }),
      ),
    );
    const client = new QueryClient();
    const withClient = (ui: ReactElement) =>
      render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
    const { container } = withClient(<StepReview />);
    expect(screen.getByText(/the app will show what is sent/)).toBeVisible();
    // Nothing runs and nothing is proposed yet: the two other parts take no room.
    expect(container.firstElementChild?.childElementCount).toBe(1);
    expect(render(<RunProgress />).container).toBeEmptyDOMElement();
    expect(withClient(<ProposalList />).container).toBeEmptyDOMElement();
    expect(render(<RunStart />).container).not.toBeEmptyDOMElement();
  });
});
