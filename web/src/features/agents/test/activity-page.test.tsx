import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ASSESS_RUN_ID,
  FAILED_RUN_ID,
  PRIVATE_RUN_ID,
  PRIVATE_STEPS,
  RUN_UNDER_WAY,
  RUN_UNDER_WAY_ID,
  SEARCH_RUN_ID,
  TRIAGE_STEP_ID,
  WEBSITE_STEPS,
  WEBSITE_STEP_IDS,
  fakeActivityApi,
} from "@/design-system/fixtures/activity";
import { FINISHED_STEPS, RUNNING_STEPS } from "@/design-system/fixtures/runs";
import { http, HttpResponse } from "msw";

import { makeRun } from "@/design-system/fixtures/runs";
import { admin, fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";
import { FakeEventSource } from "@/test/fake-event-source";
import { renderApp } from "@/test/render";

import type { RunStep } from "../api/runs-api";

const runs = () => screen.findByRole("list", { name: "Workflow runs" });

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

/** The open step's log streams `steps`, then ends unless it is still `open`. */
async function stream(steps: RunStep[], open = false) {
  await screen.findByTestId("run-panel");
  await waitFor(() => {
    expect(FakeEventSource.last).toBeDefined();
  });
  const source = FakeEventSource.last!;
  act(() => {
    source.open();
    for (const one of steps) source.emit("step", one);
    if (!open) source.emit("end", {});
  });
}

describe("Activity, the workflow runs", () => {
  it("lists every workflow on the side and each run with its trigger, newest first", async () => {
    fakeSession(member);
    fakeActivityApi();
    renderApp("/activity");
    const list = await runs();
    const rows = within(list).getAllByRole("link");
    expect(rows[0]).toHaveTextContent("Analytics engineer at Kestrel Health");
    expect(rows[0]).toHaveTextContent("Your click on Write now");
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

  it("offers Retry when the activity could not be loaded", async () => {
    fakeSession(member);
    fakeActivityApi({ runs: [] });
    let fail = true;
    server.use(
      http.get("*/api/activity/runs", () =>
        fail
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ items: [] }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/activity");
    expect(
      await screen.findByText(
        /The activity could not be loaded\. Something went wrong on the server\./,
      ),
    ).toBeVisible();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
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
      step: { steps: RUNNING_STEPS, open: true },
    });
    renderApp(`/activity/runs/${RUN_UNDER_WAY_ID}`);
    await stream(RUNNING_STEPS, true);
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
    expect(screen.getByText("Your click on Write now")).toBeInTheDocument();
    // The summary: trigger, who, status, total duration, tokens and amount, summed.
    const summary = screen.getByText("Total duration").closest("dl")!;
    for (const fact of [
      "Triggered by",
      "Who",
      "You",
      "Status",
      "Tokens",
      "48,210 in, 3,904 out",
      "$0.31",
    ])
      expect(summary).toHaveTextContent(fact);
    expect(await screen.findByTestId("run-panel")).toHaveAttribute(
      "data-status",
      "running",
    );
  });

  it("says which step failed and why", async () => {
    fakeSession(member);
    const failed: RunStep[] = [
      ...RUNNING_STEPS,
      {
        seq: 7,
        at: "2026-11-05T08:18:51Z",
        kind: "status",
        data: { status: "failed", error: "max_turns" },
      },
    ];
    fakeActivityApi({
      step: { changes: { error: "max_turns" }, steps: failed },
    });
    renderApp(`/activity/runs/${FAILED_RUN_ID}`);
    await stream(failed);
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
    fakeActivityApi({ step: { steps: FINISHED_STEPS } });
    const user = userEvent.setup();
    renderApp(`/activity/runs/${ASSESS_RUN_ID}?step=${TRIAGE_STEP_ID}`);
    await stream(FINISHED_STEPS);
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
    // The groups kept, not their buttons: an opened tool call holds its result's own toggle.
    expect(
      Array.from(log.children).map((group) => group.getAttribute("data-kind")),
    ).toEqual(["tool_call", "text"]);
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
      step: { steps: PRIVATE_STEPS },
    });
    const user = userEvent.setup();
    renderApp(`/activity/runs/${PRIVATE_RUN_ID}`);
    await stream(PRIVATE_STEPS);
    await user.click(
      await screen.findByRole("button", { name: /Tool call: read_document/ }),
    );
    expect(screen.getByText("64 characters of arguments")).toBeInTheDocument();
    expect(screen.getByText("18,342 characters")).toBeInTheDocument();
  });

  it("marks the open step in the graph too, and opens the log of the step chosen", async () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: FINISHED_STEPS } });
    renderApp(`/activity/runs/${ASSESS_RUN_ID}?step=${TRIAGE_STEP_ID}`);
    await stream(FINISHED_STEPS);
    const graph = screen.getByRole("figure", { name: "Assess, workflow" });
    const marked = within(graph).getByRole("link", { current: "step" });
    expect(marked).toHaveTextContent("Triage");
    expect(marked.closest("[data-card]")).toHaveAttribute("data-selected");
    expect(graph.querySelectorAll("[data-selected]")).toHaveLength(1);
    // The log is the chosen step's: its agent in the title, its status and time.
    const log = screen.getByTestId("run-panel");
    expect(within(log).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Triage",
    );
    expect(log).toHaveAttribute("data-status", "done");
  });

  it("counts the websites a sweep searched side by side, and marks the one whose log is open", async () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: WEBSITE_STEPS } });
    const user = userEvent.setup();
    const { location } = renderApp(
      `/activity/runs/${SEARCH_RUN_ID}?step=${WEBSITE_STEP_IDS[1]}`,
    );
    await stream(WEBSITE_STEPS);
    const graph = screen.getByRole("figure", { name: "Job search, workflow" });
    const websites = graph.querySelector('[data-card="browser_discovery"]');
    expect(websites).toHaveAttribute("data-selected");
    expect(websites).toHaveTextContent("×2");
    // From the first start to the last end: the two ran side by side, 8 min 37 s for the longer.
    expect(websites).toHaveTextContent("8 min 37 s");
    expect(
      within(
        screen.getByRole("list", { name: "Job search, this run" }),
      ).getByText("Website search: Succeeded, 2 runs, 8 min 37 s."),
    ).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Steps of this run" });
    expect(
      within(steps).getByRole("link", { current: "step" }),
    ).toHaveTextContent("Website search: Sample Careers");
    // The other website's row opens its own log; the graph's card stays marked.
    await user.click(
      within(steps).getByRole("link", {
        name: /Website search: Example Jobs/,
      }),
    );
    expect(location()).toBe(
      `/activity/runs/${SEARCH_RUN_ID}?step=${WEBSITE_STEP_IDS[0]}`,
    );
    expect(
      within(steps).getByRole("link", { current: "step" }),
    ).toHaveTextContent("Website search: Example Jobs");
    expect(websites).toHaveAttribute("data-selected");
  });

  it("gives the hiring panel's card its total time and opens it on the reviewer at work", async () => {
    fakeSession(member);
    fakeActivityApi({ step: { steps: RUNNING_STEPS, open: true } });
    renderApp(`/activity/runs/${RUN_UNDER_WAY_ID}`);
    await stream(RUNNING_STEPS, true);
    const graph = screen.getByRole("figure", {
      name: "Documents for a posting, workflow",
    });
    const panel = within(graph).getByRole("button", { name: /Hiring panel/ });
    // Opened on its member at work, the card marked; its time is the group's, not "7 agents".
    expect(panel).toHaveAttribute("aria-expanded", "true");
    expect(panel).not.toHaveTextContent("7 agents");
    expect(panel.closest("[data-card]")).toHaveAttribute("data-selected");
    expect(
      within(graph).getByRole("link", { current: "step" }),
    ).toHaveTextContent("reader, the CEO");
    expect(
      within(
        screen.getByRole("list", {
          name: "Documents for a posting, this run",
        }),
      ).getByText(/^Hiring panel: Running, .+ in all\.$/),
    ).toBeInTheDocument();
    expect(RUN_UNDER_WAY.steps.some((step) => step.node === "panel")).toBe(
      true,
    );
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

describe("a test run", () => {
  it("starts with the message typed and opens its log", async () => {
    fakeSession(admin);
    fakeActivityApi();
    const id = "22222222-2222-4222-8222-222222222222";
    const starts: unknown[] = [];
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json({
          items: [
            {
              name: "echo",
              description: "Test agent",
              model: "claude-opus-5-5",
              max_turns: 4,
              wall_time_s: 600,
              test_only: true,
            },
          ],
        }),
      ),
      http.post("*/api/agents/runs", async ({ request }) => {
        starts.push(await request.json());
        return HttpResponse.json(
          makeRun({ id, agent: "echo", status: "queued" }),
          {
            status: 202,
          },
        );
      }),
    );
    const user = userEvent.setup();
    const { location } = renderApp("/activity");
    const message = await screen.findByLabelText("Message");
    await user.clear(message);
    await user.click(screen.getByRole("button", { name: "Start test run" }));
    expect(starts).toEqual([]);
    await user.type(message, "  Bonjour ");
    await user.selectOptions(
      screen.getByLabelText("Recorded stream"),
      "Reads a job with get_job",
    );
    await user.click(screen.getByRole("button", { name: "Start test run" }));
    await waitFor(() => {
      expect(location()).toBe(`/activity/runs/${id}?step=${id}`);
    });
    expect(starts).toEqual([
      { agent: "echo", input: { message: "Bonjour", script: "tool" } },
    ]);
  });
  it("says why it did not start", async () => {
    fakeSession(admin);
    fakeActivityApi();
    server.use(
      http.get("*/api/agents", () =>
        HttpResponse.json({
          items: [
            {
              name: "echo",
              description: "Test agent",
              model: "claude-opus-5-5",
              max_turns: 4,
              wall_time_s: 600,
              test_only: true,
            },
          ],
        }),
      ),
      http.post("*/api/agents/runs", () =>
        HttpResponse.json(
          { detail: "No model credential is saved." },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    const { location } = renderApp("/activity");
    await user.click(
      await screen.findByRole("button", { name: "Start test run" }),
    );
    expect(
      await screen.findByText(/No model credential is saved/),
    ).toBeInTheDocument();
    expect(location()).toBe("/activity");
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
    fakeActivityApi({ step: { steps: FINISHED_STEPS } });
    const { location } = renderApp("/runs/8c4f");
    await waitFor(() => {
      expect(location()).toBe(`/activity/runs/${FAILED_RUN_ID}?step=8c4f`);
    });
    const steps = await screen.findByRole("list", {
      name: "Steps of this run",
    });
    expect(
      within(steps).getByRole("link", { current: "step" }),
    ).toHaveTextContent("Answer writer");
  });
});
