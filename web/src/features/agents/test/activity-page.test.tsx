import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  FAILED_RUN_ID,
  PRIVATE_RUN_ID,
  PRIVATE_STEPS,
  REVIEWER_STEP_ID,
  RUN_UNDER_WAY_ID,
  fakeActivityApi,
} from "@/design-system/fixtures/activity";
import {
  FINISHED_STEPS,
  RUNNING_STEPS,
  makeRun,
} from "@/design-system/fixtures/runs";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

const runs = () => screen.findByRole("list", { name: "Workflow runs" });

describe("Activity, the workflow runs", () => {
  it("lists every workflow on the side and each run with its trigger, newest first", async () => {
    fakeSession(member);
    fakeActivityApi();
    renderApp("/activity");
    const list = await runs();
    const rows = within(list).getAllByRole("link");
    expect(rows[0]).toHaveTextContent("Analytics engineer at Kestrel Health");
    expect(rows[0]).toHaveTextContent("Your click on Write");
    expect(rows[0]).toHaveAttribute(
      "href",
      `/activity/runs/${RUN_UNDER_WAY_ID}`,
    );
    const nav = screen.getByRole("navigation", { name: "Workflows" });
    expect(
      within(nav).getByRole("link", { name: "All workflows" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      await within(nav).findByRole("link", { name: "Application" }),
    ).toHaveAttribute("href", "/activity/application");
    expect(document.body.textContent).not.toMatch(/[–—]| - /);
  });

  it("keeps the runs of the workflow chosen", async () => {
    fakeSession(member);
    fakeActivityApi();
    renderApp("/activity/application");
    const list = await runs();
    expect(within(list).getAllByRole("link")).toHaveLength(1);
    expect(list).toHaveTextContent("Platform engineer at Lumen Freight");
  });

  it("says what will appear when there is nothing yet", async () => {
    fakeSession(member);
    fakeActivityApi({ runs: [] });
    renderApp("/activity");
    expect(await screen.findByText("No activity yet")).toBeInTheDocument();
  });

  it("shows it loads while the runs are on their way", async () => {
    fakeSession(member);
    fakeActivityApi({ loading: true });
    renderApp("/activity");
    expect(
      await screen.findByRole("status", { name: "Loading the workflow runs" }),
    ).toBeInTheDocument();
  });
});

describe("a workflow run's page", () => {
  it("shows where a run under way is: done, at work and still to come", async () => {
    fakeSession(member);
    fakeActivityApi({
      step: {
        detail: makeRun({
          id: REVIEWER_STEP_ID,
          agent: "documents_panel_reader_ceo",
          status: "running",
          finished_at: null,
          duration_s: null,
        }),
        steps: RUNNING_STEPS,
        open: true,
      },
    });
    renderApp(`/activity/runs/${RUN_UNDER_WAY_ID}`);
    const steps = await screen.findByRole("list", {
      name: "Steps of this run",
    });
    const items = within(steps).getAllByRole("listitem");
    expect(items[0]).toHaveAttribute("data-status", "success");
    expect(
      within(steps).getByRole("link", { current: "step" }),
    ).toHaveTextContent("Hiring panel: reader, the CEO");
    const graph = screen.getByRole("list", {
      name: "Documents for a posting, this run",
    });
    expect(graph).toHaveTextContent("CV writer: Succeeded");
    expect(graph).toHaveTextContent("Letter writer: Waiting.");
    expect(screen.getByText("Your click on Write")).toBeInTheDocument();
    expect(await screen.findByTestId("run-panel")).toHaveAttribute(
      "data-status",
      "running",
    );
  });

  it("says which step failed and why", async () => {
    fakeSession(member);
    fakeActivityApi({
      step: {
        detail: makeRun({
          agent: "apply_answer",
          status: "failed",
          error: "max_turns",
        }),
        steps: [
          ...RUNNING_STEPS,
          {
            seq: 7,
            at: "2026-11-05T08:18:51Z",
            kind: "status",
            data: { status: "failed", error: "max_turns" },
          },
        ],
      },
    });
    renderApp(`/activity/runs/${FAILED_RUN_ID}`);
    const steps = await screen.findByRole("list", {
      name: "Steps of this run",
    });
    expect(
      within(steps)
        .getAllByRole("listitem")
        .map((item) => item.dataset.status),
    ).toEqual(["failure", "failure"]);
    expect(
      screen.getByText("The form was left as it was: nothing was sent."),
    ).toBeInTheDocument();
    expect(await screen.findByTestId("run-panel")).toHaveAttribute(
      "data-status",
      "failed",
    );
  });

  it("folds a tool call and filters the log", async () => {
    fakeSession(member);
    fakeActivityApi({ step: { detail: makeRun(), steps: FINISHED_STEPS } });
    const user = userEvent.setup();
    renderApp(`/activity/runs/${FAILED_RUN_ID}`);
    const call = await screen.findByRole("button", {
      name: /Tool call: get_job/,
    });
    expect(call).toHaveAttribute("aria-expanded", "false");
    await user.click(call);
    expect(call).toHaveAttribute("aria-expanded", "true");
    await user.type(
      screen.getByRole("searchbox", { name: "Filter the log" }),
      "Looker",
    );
    const log = screen.getByRole("list", { name: "Steps" });
    expect(within(log).getAllByRole("button")).toHaveLength(2);
    await user.clear(screen.getByRole("searchbox", { name: "Filter the log" }));
    await user.type(
      screen.getByRole("searchbox", { name: "Filter the log" }),
      "nowhere in it",
    );
    expect(
      screen.getByText("No line of the log holds these words."),
    ).toBeInTheDocument();
  });

  it("shows a private run's steps by name and size only", async () => {
    fakeSession(member);
    fakeActivityApi({
      step: {
        detail: makeRun({ agent: "profile_extract" }),
        steps: PRIVATE_STEPS,
      },
    });
    const user = userEvent.setup();
    renderApp(`/activity/runs/${PRIVATE_RUN_ID}`);
    await user.click(
      await screen.findByRole("button", { name: /Tool call: read_document/ }),
    );
    expect(screen.getByText("64 characters of arguments")).toBeInTheDocument();
    expect(screen.getByText("18,342 characters")).toBeInTheDocument();
  });

  it("says a run that no longer exists is gone", async () => {
    fakeSession(member);
    fakeActivityApi();
    renderApp("/activity/runs/0f0e0d0c-0b0a-4908-8706-050403020100");
    expect(
      await screen.findByText("This run no longer exists"),
    ).toBeInTheDocument();
  });
});

describe("the old addresses", () => {
  it("lead /runs to Activity", async () => {
    fakeSession(member);
    fakeActivityApi();
    const { location } = renderApp("/runs");
    await waitFor(() => {
      expect(location()).toBe("/activity");
    });
  });

  it("lead /runs/:id to its workflow run with that step open", async () => {
    fakeSession(member);
    fakeActivityApi({ step: { detail: makeRun(), steps: FINISHED_STEPS } });
    const { location } = renderApp("/runs/8c4f");
    await waitFor(() => {
      expect(location()).toBe(`/activity/runs/${FAILED_RUN_ID}?step=8c4f`);
    });
    expect(
      await screen.findByRole("link", { current: "step" }),
    ).toHaveTextContent("Answer writer");
  });
});
