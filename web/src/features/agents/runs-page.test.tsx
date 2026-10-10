import { screen, waitFor, within } from "@testing-library/react";
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
});
