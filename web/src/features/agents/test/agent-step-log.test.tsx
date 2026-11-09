/**
 * The log of an agent step on a workflow run's page: the cases of the Runs page's run panel
 * (runs-page.test.tsx before Activity) that still hold, at /activity/runs/:id?step=:step.
 */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { FakeEventSource } from "@/test/fake-event-source";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { WorkflowRunDetail } from "../api/activity-api";
import type { Run } from "../api/runs-api";

const ID = "22222222-2222-4222-8222-222222222222";
const WORKFLOW_RUN = "33333333-3333-4333-8333-333333333333";
const PAGE = `/activity/runs/${WORKFLOW_RUN}?step=${ID}`;

function run(changes: Partial<Run> = {}): Run {
  return {
    id: ID,
    agent: "echo",
    job_id: null,
    status: "running",
    input: { message: "hi" },
    output: null,
    error: null,
    model: "claude-opus-5-5",
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    cancel_requested: false,
    created_at: "2026-10-10T08:00:00Z",
    started_at: "2026-10-10T08:00:01Z",
    finished_at: null,
    duration_s: null,
    ...changes,
  };
}

/** A test agent's run: alone in Other runs, one agent step, no graph. */
const workflowRun = (
  status: WorkflowRunDetail["status"],
): WorkflowRunDetail => ({
  id: WORKFLOW_RUN,
  workflow: "other",
  workflow_title: "Other runs",
  trigger: { kind: "unknown", words: "Not recorded for this run", node: null },
  subject: null,
  status,
  created_at: "2026-10-10T08:00:00Z",
  started_at: "2026-10-10T08:00:01Z",
  finished_at: null,
  duration_s: null,
  agent_runs: 1,
  flow: null,
  steps: [
    {
      id: ID,
      kind: "agent",
      node: null,
      name: "Echo",
      agent: "echo",
      status,
      started_at: "2026-10-10T08:00:01Z",
      finished_at: null,
      duration_s: null,
      private: false,
      note: null,
    },
  ],
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
  credential_kind: null,
});

function step(seq: number, kind: string, data: Record<string, unknown> = {}) {
  return { seq, at: "2026-10-10T08:00:02Z", kind, data };
}

/** The fake api of the page: the workflow run, the step's agent run, its cancel recorded. */
function stepApi(detail: Run = run()) {
  fakeSession(admin);
  const state = { detail, cancels: 0 };
  server.use(
    http.get(`*/api/activity/runs/${WORKFLOW_RUN}`, () =>
      HttpResponse.json(
        workflowRun(state.detail.status === "done" ? "success" : "running"),
      ),
    ),
    http.get(`*/api/agents/runs/${ID}`, () =>
      HttpResponse.json({
        ...state.detail,
        events: [],
        events_truncated: false,
      }),
    ),
    http.post(`*/api/agents/runs/${ID}/cancel`, ({ request }) => {
      expect(request.headers.get("X-Requested-With")).toBe("jobapp");
      state.cancels += 1;
      state.detail = { ...state.detail, cancel_requested: true };
      return HttpResponse.json(state.detail, { status: 202 });
    }),
  );
  return state;
}

/** The log's groups, each a step of the log as GitHub folds them. */
const groupsOf = (log: HTMLElement) =>
  Array.from(log.children) as HTMLElement[];

async function openLog() {
  const panel = await screen.findByTestId("run-panel");
  await waitFor(() => {
    expect(FakeEventSource.last).toBeDefined();
  });
  return { panel, source: FakeEventSource.last! };
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

describe("an agent step's log", () => {
  it("shows the steps as they stream, then the totals, and hides cancel at the end", async () => {
    const state = stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    expect(source.url).toBe(`/api/agents/runs/${ID}/stream?after=0`);
    act(() => {
      source.open();
    });
    expect(await within(panel).findByText("Live")).toBeInTheDocument();
    act(() => {
      source.emit("step", step(1, "status", { status: "queued" }));
      source.emit("step", step(2, "status", { status: "running" }));
      source.emit(
        "step",
        step(3, "init", {
          model: "claude-opus-5-5",
          tools: ["mcp__app__get_job"],
        }),
      );
      source.emit("step", step(4, "thinking"));
      source.emit("step", step(5, "text", { text: "Let me read the job." }));
      source.emit(
        "step",
        step(6, "tool_call", {
          id: "t1",
          name: "mcp__app__get_job",
          input: { job_id: "abc" },
        }),
      );
      source.emit(
        "step",
        step(7, "tool_result", {
          id: "t1",
          is_error: false,
          content: '{"company": "Acme"}',
        }),
      );
      source.emit("step", step(8, "tool_denied", { name: "Bash" }));
      source.emit(
        "step",
        step(9, "tool_result", {
          id: "t2",
          is_error: true,
          content: "The tool Bash is not available to this agent.",
        }),
      );
    });
    const log = within(panel).getByRole("list", { name: "Steps" });
    // Set up, thinking, the answer, the call with its result, the refusal, the error.
    await waitFor(() => {
      expect(groupsOf(log).map((group) => group.dataset.kind)).toEqual([
        "status",
        "thinking",
        "text",
        "tool_call",
        "tool_denied",
        "tool_result",
      ]);
    });
    expect(within(log).getByText("Started on claude-opus-5-5")).toBeVisible();
    expect(within(log).getByText("Tools: get_job")).toBeVisible();
    expect(within(log).getAllByText("Let me read the job.")).not.toHaveLength(
      0,
    );
    expect(
      within(log).getByRole("button", { name: /Tool call: get_job/ }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(within(log).getAllByText("Refused: Bash")).not.toHaveLength(0);
    expect(within(log).getAllByText("Tool error")).not.toHaveLength(0);
    expect(panel.querySelector("[data-slot=badge]")).toHaveTextContent(
      "Running",
    );

    // Cancel: the request goes out, the button then says the run is stopping.
    const user = userEvent.setup();
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(state.cancels).toBe(1);
    });
    expect(
      await within(panel).findByRole("button", { name: "Stopping" }),
    ).toBeDisabled();

    // The run ends: the detail is read again for the totals.
    state.detail = run({
      status: "cancelled",
      cancel_requested: true,
      input_tokens: 300,
      output_tokens: 12,
      cost_usd: 0.25,
      finished_at: "2026-10-10T08:00:09Z",
      duration_s: 8,
    });
    act(() => {
      source.emit(
        "step",
        step(10, "usage", {
          input_tokens: 300,
          output_tokens: 12,
          cost_usd: 0.25,
        }),
      );
      source.emit("step", step(11, "status", { status: "cancelled" }));
      source.emit("end", {});
    });
    const facts = within(panel).getByTestId("run-facts");
    await waitFor(() => {
      expect(within(facts).getByText("$0.25")).toBeInTheDocument();
    });
    expect(within(facts).getByText("300 in, 12 out")).toBeInTheDocument();
    expect(within(facts).getByText("8.0 s")).toBeInTheDocument();
    expect(within(panel).getByText("Ended")).toBeInTheDocument();
    expect(
      within(panel).queryByRole("button", { name: /^(Cancel|Stopping)$/ }),
    ).not.toBeInTheDocument();
  });

  it("shows a failed run's error in its log", async () => {
    stepApi(
      run({ status: "failed", error: "The agent stopped without a result." }),
    );
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.emit(
        "step",
        step(1, "status", {
          status: "failed",
          error: "The agent stopped without a result.",
        }),
      );
      source.emit("end", {});
    });
    expect(
      await within(panel).findByText("The agent stopped without a result."),
    ).toBeInTheDocument();
    expect(panel.querySelector("[data-slot=badge]")).toHaveTextContent(
      "Failed",
    );
  });

  it("says when the step's log could not be loaded", async () => {
    stepApi();
    server.use(
      http.get(`*/api/agents/runs/${ID}`, () =>
        HttpResponse.json({ detail: "Run not found." }, { status: 404 }),
      ),
    );
    renderApp(PAGE);
    expect(
      await screen.findByText(/This step's log could not be loaded/),
    ).toBeInTheDocument();
  });

  it("shows the reconnecting state", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.fail(false);
    });
    expect(await within(panel).findByText("Reconnecting")).toBeInTheDocument();
    act(() => {
      source.open();
    });
    expect(await within(panel).findByText("Live")).toBeInTheDocument();
  });

  it("shows the size of a private tool call and result, with no content, and leaves a stored one as it was", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit(
        "step",
        step(2, "tool_call", {
          id: "p1",
          name: "mcp__app__read",
          input_size: 42,
        }),
      );
      source.emit(
        "step",
        step(3, "tool_result", {
          id: "p1",
          is_error: false,
          size: 1234,
          blocks: 2,
        }),
      );
      source.emit("step", step(4, "text", { text: "Read." }));
      source.emit(
        "step",
        step(5, "tool_result", {
          id: "s1",
          is_error: false,
          content: "stored text",
        }),
      );
    });
    const log = within(panel).getByRole("list", { name: "Steps" });
    const user = userEvent.setup();
    await user.click(
      await within(log).findByRole("button", { name: /Tool call: read/ }),
    );
    const [, call] = groupsOf(log) as [HTMLElement, HTMLElement];
    expect(call).toHaveTextContent("42 characters of arguments");
    expect(call).toHaveTextContent("1,234 characters, 2 other blocks");
    expect(call.querySelector("pre")).toBeNull();
    expect(call.querySelector("[data-slot=trace-collapsible]")).toBeNull();
    // A stored result keeps its disclosure and its text, folded until it is opened.
    const stored = groupsOf(log).at(-1)!;
    const show = within(stored).getByRole("button", { name: "Tool result" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    expect(stored.querySelector("pre")).toBeNull();
    await user.click(show);
    expect(show).toHaveAttribute("aria-expanded", "true");
    expect(stored.querySelector("pre")).toHaveTextContent("stored text");
  });

  it("keeps the log and its steps when a refresh fails", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit("step", step(2, "text", { text: "Working on it." }));
    });
    expect(
      (await within(panel).findAllByText("Working on it.")).length,
    ).toBeGreaterThan(0);
    // The api goes away: the cancel fails, and so does the refresh that follows.
    server.use(
      http.post(`*/api/agents/runs/${ID}/cancel`, () => HttpResponse.error()),
      http.get(`*/api/agents/runs/${ID}`, () =>
        HttpResponse.json({ detail: "down" }, { status: 502 }),
      ),
    );
    const user = userEvent.setup();
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(
      await within(panel).findByText(/Could not refresh the totals/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("run-panel")).toBe(panel);
    expect(within(panel).getAllByText("Working on it.").length).toBeGreaterThan(
      0,
    );
    expect(within(panel).getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  it("moves the focus to the title when Cancel goes away, and announces only the newest step", async () => {
    const state = stepApi(run({ status: "queued" }));
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "queued" }));
      source.emit(
        "step",
        step(2, "text", {
          text: "A long answer that is not read out in full.",
        }),
      );
    });
    const announcer = await within(panel).findByText("Answer", {
      selector: "[role=status][aria-live=polite]",
    });
    expect(announcer).toHaveClass("sr-only");
    expect(
      within(panel).getByRole("list", { name: "Steps" }),
    ).not.toHaveAttribute("aria-live");

    const user = userEvent.setup();
    const button = within(panel).getByRole("button", { name: "Cancel" });
    await user.click(button);
    expect(button).toHaveFocus();
    await waitFor(() => {
      expect(state.cancels).toBe(1);
    });
    state.detail = run({ status: "cancelled", cancel_requested: true });
    act(() => {
      source.emit("step", step(3, "status", { status: "cancelled" }));
    });
    await waitFor(() => {
      expect(
        within(panel).queryByRole("button", { name: /^(Cancel|Stopping)$/ }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
    expect(announcer).toHaveTextContent("Cancelled");
  });

  it("never lists a step twice", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.emit("step", step(1, "text", { text: "once" }));
      source.emit("step", step(1, "text", { text: "once" }));
    });
    const log = within(panel).getByRole("list", { name: "Steps" });
    await waitFor(() => {
      expect(groupsOf(log).map((group) => group.dataset.kind)).toEqual([
        "text",
      ]);
    });
  });

  it("shows the answer while it is written, then the stored step in its place", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit("step", step(2, "thinking"));
    });
    const log = within(panel).getByRole("list", { name: "Steps" });
    const region = within(panel)
      .getAllByRole("status")
      .find((node) => node.getAttribute("aria-live") === "polite")!;
    await waitFor(() => {
      expect(region).toHaveTextContent("Thinking");
    });
    // An empty block is not an answer yet: no group for it.
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 0, text: "" });
    });
    expect(groupsOf(log)).toHaveLength(2);
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 0, text: "It fi" });
    });
    let answer = groupsOf(log)[2]!;
    expect(groupsOf(log)).toHaveLength(3);
    expect(answer).toHaveAttribute("data-live", "true");
    expect(answer).toHaveAttribute("aria-busy", "true");
    expect(answer).toHaveAttribute("data-kind", "text");
    expect(answer).toHaveTextContent("It fi");
    // The title is announced once, the growing text never.
    expect(region).toHaveTextContent(/^Answer$/);
    act(() => {
      source.emit("partial", {
        block: 1,
        after: 2,
        offset: 5,
        text: "ts well",
      });
    });
    expect(groupsOf(log)[2]).toHaveTextContent("It fits well");
    expect(region).toHaveTextContent(/^Answer$/);
    // The stored step replaces it: one answer, not live.
    act(() => {
      source.emit("step", step(3, "text", { text: "It fits well." }));
    });
    answer = groupsOf(log)[2]!;
    expect(groupsOf(log)).toHaveLength(3);
    expect(answer).not.toHaveAttribute("data-live");
    expect(answer).not.toHaveAttribute("aria-busy");
    expect(answer).toHaveTextContent("It fits well.");
    expect(region).toHaveTextContent(/^Answer$/);
    // A delta that arrives after the complete message changes nothing.
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 12, text: "!!" });
    });
    expect(groupsOf(log)).toHaveLength(3);
    expect(panel).not.toHaveTextContent("!!");
  });

  it("drops the unfinished answer when the run is cancelled in the middle of it", async () => {
    stepApi();
    renderApp(PAGE);
    const { panel, source } = await openLog();
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit("partial", {
        block: 1,
        after: 1,
        offset: 0,
        text: "Half an ans",
      });
    });
    expect(
      (await within(panel).findAllByText("Half an ans")).length,
    ).toBeGreaterThan(0);
    act(() => {
      source.emit("step", step(2, "status", { status: "cancelled" }));
      source.emit("end", {});
    });
    expect(within(panel).queryByText("Half an ans")).not.toBeInTheDocument();
    const log = within(panel).getByRole("list", { name: "Steps" });
    // Set up holds the two statuses, and nothing else is left.
    expect(groupsOf(log).map((group) => group.dataset.kind)).toEqual([
      "status",
    ]);
    expect(
      groupsOf(log)[0]!.querySelectorAll('[role=listitem][data-kind="status"]'),
    ).toHaveLength(2);
  });

  /**
   * Issue #33: on a subscription the amount is an estimate, not a bill (issue #80); with an API
   * key it is the cost; a run with no recorded kind is worded neutrally.
   */
  describe("the amount, by the kind of credential the run used", () => {
    const done = (changes: Partial<Run>) =>
      run({
        status: "done",
        input_tokens: 43100,
        output_tokens: 1116,
        cost_usd: 0.193,
        finished_at: "2026-10-10T08:00:15Z",
        duration_s: 13.9,
        ...changes,
      });
    const usage = step(3, "usage", {
      input_tokens: 43100,
      output_tokens: 1116,
      cost_usd: 0.193,
    });

    async function openRun(detail: Run) {
      stepApi(detail);
      renderApp(PAGE);
      const { panel, source } = await openLog();
      act(() => {
        source.open();
        source.emit("step", step(1, "status", { status: "running" }));
        source.emit("step", usage);
      });
      const log = within(panel).getByRole("list", { name: "Steps" });
      const metrics = within(panel).getByTestId("run-facts");
      await waitFor(() => {
        expect(metrics).toHaveTextContent("$0.19");
      });
      return { panel, log, metrics };
    }

    it("is the cost with an API key", async () => {
      const { panel, log, metrics } = await openRun(
        done({ credential_kind: "anthropic_api_key" }),
      );
      expect(within(metrics).getByText("Cost")).toBeInTheDocument();
      expect(metrics).toHaveTextContent("Cost$0.19");
      expect(within(panel).queryByTestId("cost-note")).not.toBeInTheDocument();
      expect(log).toHaveTextContent("43,100 in, 1,116 out, $0.19");
      expect(panel).not.toHaveTextContent(/estimate|not billed|not a bill/i);
    });

    it("is an estimate at API prices, not a bill, with a subscription", async () => {
      const { panel, log, metrics } = await openRun(
        done({ credential_kind: "claude_subscription" }),
      );
      expect(metrics).toHaveTextContent("Estimate at API prices$0.19");
      expect(within(metrics).queryByText("Cost")).not.toBeInTheDocument();
      expect(within(panel).getByTestId("cost-note")).toHaveTextContent(
        "This run used your Claude subscription and counts toward your plan's usage limits. What you pay is set by your plan, not by this amount: the estimate is what the same tokens cost at API prices.",
      );
      expect(log).toHaveTextContent(
        "43,100 in, 1,116 out, $0.19 estimated at API prices, not a bill",
      );
      expect(panel.textContent).not.toMatch(/[–—]| - /);
    });

    it.each([
      ["null", { credential_kind: null }],
      ["absent, from an older api", {}],
    ])(
      "is worded neutrally when the kind is %s",
      async (_name, changes: Partial<Run>) => {
        const { panel, log, metrics } = await openRun(done(changes));
        expect(metrics).toHaveTextContent("Amount at API prices$0.19");
        expect(within(metrics).queryByText("Cost")).not.toBeInTheDocument();
        expect(within(panel).getByTestId("cost-note")).toHaveTextContent(
          "The kind of credential this run used was not recorded, so whether this amount was billed is not known.",
        );
        expect(log).toHaveTextContent(
          "43,100 in, 1,116 out, $0.19 at API prices",
        );
        expect(metrics).not.toHaveTextContent(
          /estimate|not billed|not a bill/i,
        );
      },
    );

    /**
     * Issue #80: the usage step says which kind of credential the run used, so its line is
     * worded right the moment it arrives, before the detail (read while queued) knows it.
     */
    it.each([
      [
        "claude_subscription",
        "43,100 in, 1,116 out, $0.19 estimated at API prices, not a bill",
      ],
      ["anthropic_api_key", "43,100 in, 1,116 out, $0.19"],
    ])(
      "words the usage step by the kind it carries (%s), before the detail knows it",
      async (kind, line) => {
        stepApi(run({ status: "running", credential_kind: null }));
        renderApp(PAGE);
        const { panel, source } = await openLog();
        act(() => {
          source.open();
          source.emit("step", step(1, "status", { status: "running" }));
          source.emit(
            "step",
            step(3, "usage", {
              input_tokens: 43100,
              output_tokens: 1116,
              cost_usd: 0.193,
              credential_kind: kind,
            }),
          );
        });
        const log = within(panel).getByRole("list", { name: "Steps" });
        // The very first wording of the line is the right one: no neutral wording in between.
        const [item] = await within(log).findAllByText(/43,100 in/);
        expect(item!.textContent).toBe(line);
      },
    );

    it("keeps the run's own kind for a usage step that carries none or one it does not know", async () => {
      for (const carried of [{}, { credential_kind: "something_new" }]) {
        stepApi(done({ credential_kind: "claude_subscription" }));
        const view = renderApp(PAGE);
        const { panel, source } = await openLog();
        act(() => {
          source.open();
          source.emit(
            "step",
            step(3, "usage", {
              input_tokens: 43100,
              output_tokens: 1116,
              cost_usd: 0.193,
              ...carried,
            }),
          );
        });
        const log = within(panel).getByRole("list", { name: "Steps" });
        expect(
          (
            await within(log).findAllByText(
              "43,100 in, 1,116 out, $0.19 estimated at API prices, not a bill",
            )
          ).length,
        ).toBeGreaterThan(0);
        view.unmount();
        FakeEventSource.reset();
      }
    });

    it("ties the note to the amount for assistive technology", async () => {
      const { panel, metrics } = await openRun(
        done({ credential_kind: "claude_subscription" }),
      );
      const amount = within(metrics).getByText("$0.19");
      const note = within(panel).getByTestId("cost-note");
      expect(amount.tagName).toBe("DD");
      expect(note.id).not.toBe("");
      expect(amount).toHaveAttribute("aria-describedby", note.id);
      // The other values are described by nothing.
      for (const other of metrics.querySelectorAll("dd")) {
        if (other !== amount)
          expect(other).not.toHaveAttribute("aria-describedby");
      }
    });

    it("says only that there is no amount yet while the run has none", async () => {
      stepApi(run({ credential_kind: "claude_subscription" }));
      renderApp(PAGE);
      const { panel } = await openLog();
      const metrics = within(panel).getByTestId("run-facts");
      expect(metrics).toHaveTextContent("CostNone yet");
      expect(within(panel).queryByTestId("cost-note")).not.toBeInTheDocument();
    });
  });
});
