import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { FakeEventSource } from "@/test/fake-event-source";
import type { Agent, Run } from "./runs-api";

const ID = "22222222-2222-4222-8222-222222222222";

const echo: Agent = {
  name: "echo",
  description: "Test agent",
  model: "claude-opus-5-5",
  max_turns: 4,
  wall_time_s: 600,
  test_only: true,
};

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

function step(seq: number, kind: string, data: Record<string, unknown> = {}) {
  return { seq, at: "2026-10-10T08:00:02Z", kind, data };
}

/** A fake /api/agents: the given agents and runs, a run detail, start and cancel recorded. */
function agentsApi(
  options: { agents?: Agent[]; runs?: Run[]; detail?: Run } = {},
) {
  fakeSession(admin);
  const state = {
    detail: options.detail ?? run(),
    starts: [] as Array<Record<string, unknown>>,
    cancels: 0,
  };
  server.use(
    http.get("*/api/agents", () =>
      HttpResponse.json({ items: options.agents ?? [] }),
    ),
    http.get("*/api/agents/runs", () =>
      HttpResponse.json({ items: options.runs ?? [] }),
    ),
    http.get(`*/api/agents/runs/${ID}`, () =>
      HttpResponse.json({
        ...state.detail,
        events: [],
        events_truncated: false,
      }),
    ),
    http.post("*/api/agents/runs", async ({ request }) => {
      state.starts.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(run({ status: "queued" }), { status: 202 });
    }),
    http.post(`*/api/agents/runs/${ID}/cancel`, ({ request }) => {
      expect(request.headers.get("X-Requested-With")).toBe("jobapp");
      state.cancels += 1;
      state.detail = { ...state.detail, cancel_requested: true };
      return HttpResponse.json(state.detail, { status: 202 });
    }),
  );
  return state;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

describe("/runs", () => {
  it("lists runs with status, tokens, cost and duration", async () => {
    agentsApi({
      runs: [
        run({
          status: "done",
          input_tokens: 4520,
          output_tokens: 42,
          cost_usd: 0.0123,
          finished_at: "2026-10-10T08:00:05Z",
          duration_s: 4.2,
        }),
        run({
          id: "33333333-3333-4333-8333-333333333333",
          status: "failed",
          error: "The run reached its limit of 4 turns.",
        }),
      ],
    });
    renderApp("/runs");
    const list = await screen.findByRole("list", { name: "Runs" });
    const items = within(list).getAllByRole("link");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Done");
    expect(items[0]).toHaveTextContent("4,520 in, 42 out");
    expect(items[0]).toHaveTextContent("$0.0123");
    expect(items[0]).toHaveTextContent("4.2 s");
    expect(items[0]).toHaveAttribute("href", `/runs/${ID}`);
    expect(items[1]).toHaveTextContent("Failed");
    expect(items[1]).toHaveTextContent("limit of 4 turns");
    // No test agent offered (production): no test run card.
    expect(screen.queryByText("Test run")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("says when there is no run", async () => {
    agentsApi();
    renderApp("/runs");
    expect(await screen.findByText(/No run yet/)).toBeInTheDocument();
  });

  it("starts a test run and opens it", async () => {
    const state = agentsApi({ agents: [echo] });
    const user = userEvent.setup();
    const { location } = renderApp("/runs");
    await screen.findByText("Test run");
    await user.selectOptions(
      screen.getByLabelText("Recorded stream"),
      "Reads a job with get_job",
    );
    const message = screen.getByLabelText("Message");
    await user.clear(message);
    await user.click(screen.getByRole("button", { name: "Start test run" }));
    expect(state.starts).toEqual([]); // an empty message is not sent
    await user.type(message, "  Bonjour ");
    await user.click(screen.getByRole("button", { name: "Start test run" }));
    await waitFor(() => {
      expect(location()).toBe(`/runs/${ID}`);
    });
    expect(state.starts).toEqual([
      { agent: "echo", input: { message: "Bonjour", script: "tool" } },
    ]);
  });

  it("shows why a test run did not start", async () => {
    agentsApi({ agents: [echo] });
    server.use(
      http.post("*/api/agents/runs", () =>
        HttpResponse.json(
          { detail: "No model credential is saved." },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp("/runs");
    await user.click(
      await screen.findByRole("button", { name: "Start test run" }),
    );
    expect(
      await screen.findByText(/No model credential is saved/),
    ).toBeInTheDocument();
  });
});

describe("/runs/:id, the run panel", () => {
  it("shows the steps as they stream, then the totals, and hides cancel at the end", async () => {
    const state = agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    expect(source.url).toBe(`/api/agents/runs/${ID}/stream?after=0`);
    source.open();
    expect(await within(panel).findByText("Live")).toBeInTheDocument();
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
    const steps = await within(panel).findByRole("list", { name: "Steps" });
    await waitFor(() => {
      expect(within(steps).getAllByRole("listitem")).toHaveLength(9);
    });
    expect(
      within(steps).getByText("Started on claude-opus-5-5"),
    ).toBeInTheDocument();
    expect(within(steps).getByText("Tools: get_job")).toBeInTheDocument();
    expect(within(steps).getByText("Let me read the job.")).toBeInTheDocument();
    expect(within(steps).getByText("Tool call: get_job")).toBeInTheDocument();
    expect(within(steps).getByText("Refused: Bash")).toBeInTheDocument();
    expect(within(steps).getByText("Tool error")).toBeInTheDocument();
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
    await waitFor(() => {
      expect(within(panel).getByText("$0.25")).toBeInTheDocument();
    });
    expect(within(panel).getByText("300 in, 12 out")).toBeInTheDocument();
    expect(within(panel).getByText("8.0 s")).toBeInTheDocument();
    expect(within(panel).getByText("Ended")).toBeInTheDocument();
    expect(
      within(panel).queryByRole("button", { name: /Cancel|Stopping/ }),
    ).not.toBeInTheDocument();
  });

  it("shows a failed run's error in its timeline", async () => {
    agentsApi({
      detail: run({
        status: "failed",
        error: "The agent stopped without a result.",
      }),
    });
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    source.emit(
      "step",
      step(1, "status", {
        status: "failed",
        error: "The agent stopped without a result.",
      }),
    );
    source.emit("end", {});
    expect(
      await within(panel).findByText("The agent stopped without a result."),
    ).toBeInTheDocument();
    expect(panel.querySelector("[data-slot=badge]")).toHaveTextContent(
      "Failed",
    );
  });

  it("says when the run does not exist", async () => {
    agentsApi();
    server.use(
      http.get(`*/api/agents/runs/${ID}`, () =>
        HttpResponse.json({ detail: "Run not found." }, { status: 404 }),
      ),
    );
    renderApp(`/runs/${ID}`);
    expect(
      await screen.findByText(/The run could not be loaded/),
    ).toBeInTheDocument();
  });

  it("shows the reconnecting state", async () => {
    agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    source.open();
    source.fail(false);
    expect(await within(panel).findByText("Reconnecting")).toBeInTheDocument();
    source.open();
    expect(await within(panel).findByText("Live")).toBeInTheDocument();
  });

  it("keeps the panel and its steps when a refresh fails", async () => {
    agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    source.open();
    source.emit("step", step(1, "status", { status: "running" }));
    source.emit("step", step(2, "text", { text: "Working on it." }));
    expect(
      await within(panel).findByText("Working on it."),
    ).toBeInTheDocument();
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
    expect(within(panel).getByText("Working on it.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  it("moves the focus to the title when Cancel goes away, and announces only the newest step", async () => {
    const state = agentsApi({ detail: run({ status: "queued" }) });
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    source.open();
    source.emit("step", step(1, "status", { status: "queued" }));
    source.emit(
      "step",
      step(2, "text", { text: "A long answer that is not read out in full." }),
    );
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
    source.emit("step", step(3, "status", { status: "cancelled" }));
    await waitFor(() => {
      expect(
        within(panel).queryByRole("button", { name: /Cancel|Stopping/ }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("run-title")).toHaveFocus();
    expect(announcer).toHaveTextContent("Cancelled");
  });

  it("never lists a step twice", async () => {
    agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    source.emit("step", step(1, "text", { text: "once" }));
    source.emit("step", step(1, "text", { text: "once" }));
    const steps = await within(panel).findByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(1);
  });

  it("shows the answer while it is written, then the stored step in its place", async () => {
    agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
    act(() => {
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit("step", step(2, "thinking"));
    });
    const steps = await within(panel).findByRole("list", { name: "Steps" });
    const region = within(panel)
      .getAllByRole("status")
      .find((node) => node.getAttribute("aria-live") === "polite")!;
    expect(region).toHaveTextContent("Thinking");
    // An empty block is not an answer yet: no item for it.
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 0, text: "" });
    });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 0, text: "It fi" });
    });
    let items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[2]).toHaveAttribute("data-live", "true");
    expect(items[2]).toHaveAttribute("aria-busy", "true");
    expect(items[2]).toHaveAttribute("data-kind", "text");
    expect(items[2]).toHaveTextContent("AnswerIt fi");
    // The title is announced once, the growing text never.
    expect(region).toHaveTextContent(/^Answer$/);
    const before = items[2]!.querySelector("p:last-of-type")!.className;
    act(() => {
      source.emit("partial", {
        block: 1,
        after: 2,
        offset: 5,
        text: "ts well",
      });
    });
    items = within(steps).getAllByRole("listitem");
    expect(items[2]).toHaveTextContent("AnswerIt fits well");
    expect(region).toHaveTextContent(/^Answer$/);
    // The stored step replaces it in one render: one answer, the same markup, not live.
    act(() => {
      source.emit("step", step(3, "text", { text: "It fits well." }));
    });
    items = within(steps).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[2]).not.toHaveAttribute("data-live");
    expect(items[2]).not.toHaveAttribute("aria-busy");
    expect(items[2]).toHaveTextContent("AnswerIt fits well.");
    expect(items[2]!.querySelector("p:last-of-type")!.className).toBe(before);
    expect(region).toHaveTextContent(/^Answer$/);
    // A delta that arrives after the complete message changes nothing.
    act(() => {
      source.emit("partial", { block: 1, after: 2, offset: 12, text: "!!" });
    });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(3);
    expect(panel).not.toHaveTextContent("!!");
  });

  it("drops the unfinished answer when the run is cancelled in the middle of it", async () => {
    agentsApi();
    renderApp(`/runs/${ID}`);
    const panel = await screen.findByTestId("run-panel");
    const source = FakeEventSource.last!;
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
    expect(await within(panel).findByText("Half an ans")).toBeInTheDocument();
    act(() => {
      source.emit("step", step(2, "status", { status: "cancelled" }));
      source.emit("end", {});
    });
    expect(within(panel).queryByText("Half an ans")).not.toBeInTheDocument();
    const steps = within(panel).getByRole("list", { name: "Steps" });
    expect(
      within(steps)
        .getAllByRole("listitem")
        .map((item) => item.dataset.kind),
    ).toEqual(["status", "status"]);
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
      agentsApi({ detail });
      renderApp(`/runs/${ID}`);
      const panel = await screen.findByTestId("run-panel");
      const source = FakeEventSource.last!;
      source.open();
      source.emit("step", step(1, "status", { status: "running" }));
      source.emit("step", usage);
      const steps = await within(panel).findByRole("list", { name: "Steps" });
      const metrics = panel.querySelector("dl")!;
      await waitFor(() => {
        expect(metrics).toHaveTextContent("$0.193");
      });
      return { panel, steps, metrics };
    }

    it("is the cost with an API key", async () => {
      const { panel, steps, metrics } = await openRun(
        done({ credential_kind: "anthropic_api_key" }),
      );
      expect(within(metrics).getByText("Cost")).toBeInTheDocument();
      expect(metrics).toHaveTextContent("Cost$0.193");
      expect(within(panel).queryByTestId("cost-note")).not.toBeInTheDocument();
      expect(
        within(steps).getByText("43,100 in, 1,116 out, $0.193"),
      ).toBeInTheDocument();
      expect(panel).not.toHaveTextContent(/estimate|not billed|not a bill/i);
    });

    it("is an estimate at API prices, not a bill, with a subscription", async () => {
      const { panel, steps, metrics } = await openRun(
        done({ credential_kind: "claude_subscription" }),
      );
      expect(metrics).toHaveTextContent("Estimate at API prices$0.193");
      expect(within(metrics).queryByText("Cost")).not.toBeInTheDocument();
      expect(within(panel).getByTestId("cost-note")).toHaveTextContent(
        "This run used your Claude subscription and counts toward your plan's usage limits. What you pay is set by your plan, not by this amount: the estimate is what the same tokens cost at API prices.",
      );
      expect(
        within(steps).getByText(
          "43,100 in, 1,116 out, $0.193 estimated at API prices, not a bill",
        ),
      ).toBeInTheDocument();
      expect(panel.textContent).not.toMatch(/[–—]| - /);
    });

    it.each([
      ["null", { credential_kind: null }],
      ["absent, from an older api", {}],
    ])(
      "is worded neutrally when the kind is %s",
      async (_name, changes: Partial<Run>) => {
        const { panel, steps, metrics } = await openRun(done(changes));
        expect(metrics).toHaveTextContent("Amount at API prices$0.193");
        expect(within(metrics).queryByText("Cost")).not.toBeInTheDocument();
        expect(within(panel).getByTestId("cost-note")).toHaveTextContent(
          "The kind of credential this run used was not recorded, so whether this amount was billed is not known.",
        );
        expect(
          within(steps).getByText("43,100 in, 1,116 out, $0.193 at API prices"),
        ).toBeInTheDocument();
        expect(metrics).not.toHaveTextContent(
          /estimate|not billed|not a bill/i,
        );
      },
    );

    /**
     * Issue #80: the usage step says which kind of credential the run used, so its line is
     * worded right the moment it arrives. The detail still holds no kind then (it was read
     * while the run was queued), and the line used to say "at API prices" until the refetch.
     */
    it.each([
      [
        "claude_subscription",
        "43,100 in, 1,116 out, $0.193 estimated at API prices, not a bill",
      ],
      ["anthropic_api_key", "43,100 in, 1,116 out, $0.193"],
    ])(
      "words the usage step by the kind it carries (%s), before the detail knows it",
      async (kind, line) => {
        agentsApi({
          detail: run({ status: "running", credential_kind: null }),
        });
        renderApp(`/runs/${ID}`);
        const panel = await screen.findByTestId("run-panel");
        const source = FakeEventSource.last!;
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
        const steps = await within(panel).findByRole("list", { name: "Steps" });
        // The very first wording of the line is the right one: no neutral wording in between.
        const item = await within(steps).findByText(/43,100 in/);
        expect(item).toHaveTextContent(line);
        expect(item.textContent).toBe(line);
      },
    );

    it("keeps the run's own kind for a usage step that carries none or one it does not know", async () => {
      for (const carried of [{}, { credential_kind: "something_new" }]) {
        agentsApi({ detail: done({ credential_kind: "claude_subscription" }) });
        const view = renderApp(`/runs/${ID}`);
        const panel = await screen.findByTestId("run-panel");
        const source = FakeEventSource.last!;
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
        const steps = await within(panel).findByRole("list", { name: "Steps" });
        expect(
          await within(steps).findByText(
            "43,100 in, 1,116 out, $0.193 estimated at API prices, not a bill",
          ),
        ).toBeInTheDocument();
        view.unmount();
      }
    });

    it("ties the note to the amount for assistive technology", async () => {
      const { panel, metrics } = await openRun(
        done({ credential_kind: "claude_subscription" }),
      );
      const amount = within(metrics).getByText("$0.193");
      const note = within(panel).getByTestId("cost-note");
      expect(amount.tagName).toBe("DD");
      expect(note.id).not.toBe("");
      expect(amount).toHaveAttribute("aria-describedby", note.id);
      expect(amount).toHaveAccessibleDescription(
        "This run used your Claude subscription and counts toward your plan's usage limits. What you pay is set by your plan, not by this amount: the estimate is what the same tokens cost at API prices.",
      );
      // The other values are described by nothing.
      for (const other of metrics.querySelectorAll("dd")) {
        if (other !== amount)
          expect(other).not.toHaveAttribute("aria-describedby");
      }
    });

    it("describes the amount by nothing when it is a plain cost", async () => {
      const { metrics } = await openRun(
        done({ credential_kind: "anthropic_api_key" }),
      );
      expect(within(metrics).getByText("$0.193")).not.toHaveAttribute(
        "aria-describedby",
      );
    });

    it("says only that there is no amount yet while the run has none", async () => {
      agentsApi({
        detail: run({ credential_kind: "claude_subscription" }),
      });
      renderApp(`/runs/${ID}`);
      const panel = await screen.findByTestId("run-panel");
      const metrics = panel.querySelector("dl")!;
      expect(metrics).toHaveTextContent("CostNone yet");
      expect(within(panel).queryByTestId("cost-note")).not.toBeInTheDocument();
    });

    it("qualifies each amount of the list", async () => {
      const ids = ["a", "b", "c", "d"].map(
        (letter) => `44444444-4444-4444-8444-00000000000${letter}`,
      );
      agentsApi({
        runs: [
          done({ id: ids[0], credential_kind: "anthropic_api_key" }),
          done({ id: ids[1], credential_kind: "claude_subscription" }),
          done({ id: ids[2], credential_kind: null }),
          run({
            id: ids[3],
            status: "failed",
            credential_kind: "claude_subscription",
          }),
        ],
      });
      renderApp("/runs");
      const list = await screen.findByRole("list", { name: "Runs" });
      const costs = within(list)
        .getAllByTestId("run-cost")
        .map((node) => node.textContent);
      expect(costs).toEqual([
        "$0.193",
        "$0.193Estimate, not a bill",
        "$0.193At API prices",
        "None yet",
      ]);
      expect(
        screen.getByText(/On a Claude subscription the amount is an estimate/),
      ).toBeInTheDocument();
      expect(document.body.textContent).not.toMatch(/[–—]| - /);
    });
  });
});
