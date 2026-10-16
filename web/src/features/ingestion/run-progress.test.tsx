import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { FakeEventSource } from "@/test/fake-event-source";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import {
  CONSENT_TEXT,
  fakeRun,
  fakeRuns,
  fakeStep,
  OVERSHOOT,
  REMINDER,
  type FakeRuns,
} from "@/test/ingestion-runs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { RUN_POLL_MS, runKey, type Run } from "./runs-api";

const RUNS = "/api/ingestion/runs";
const RUN = `${RUNS}/r1`;
/** No raw code, status name or dash used as a separator on the page. */
const RAW =
  /token_ceiling|paused_reason|continue_adds|continue_changed|closed_reason|\bslots\b|[–—]| - /;

async function show(run: Run, more: Partial<FakeRuns> = {}) {
  fakeSession(member);
  fakeDocuments([
    fakeDocument({ id: "d1", name: "dossier.txt" }),
    fakeDocument({ id: "d2", name: "cv.txt" }),
  ]);
  fakePrompt();
  const runs = fakeRuns({ runs: [run], ...more });
  const { client } = renderApp("/profile/fill?step=review");
  const state = await screen.findByTestId("run-state");
  return { runs, state, client, user: userEvent.setup() };
}

const ceiling = (adds: number | null, over: Partial<Run> = {}) =>
  fakeRun({
    status: "paused",
    paused_reason: "token_ceiling",
    tokens: { spent: 380_000, ceiling: 352_440, continue_adds: adds },
    counts: { running: 0, planned: 1 },
    steps: [
      fakeStep({ position: 0, status: "done", items: 1, tokens: 380_000 }),
      fakeStep({
        position: 1,
        document_id: "d2",
        document_name: "cv.txt",
      }),
    ],
    ...over,
  });

const panel = () =>
  document.querySelector('[data-step="review"]') as HTMLElement;

/** The 2 second read of the run, once, on the test's clock (setInterval is faked). */
async function poll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(RUN_POLL_MS);
  });
}

/** The engine's sentence for a run it closed because it could not move it (engine.STUCK). */
const STUCK =
  "This run was closed because an error on our side kept it from going on. The documents it had finished reading are kept, and a new run does not read them again.";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

describe("the run on the page", () => {
  it("finds its run from the list and the run route, and says where it is", async () => {
    const { runs, state } = await show(fakeRun());
    expect(runs.sent("GET", RUNS)).not.toHaveLength(0);
    expect(runs.sent("GET", RUN)).not.toHaveLength(0);
    expect(state).toHaveAttribute("role", "status");
    expect(state).toHaveAttribute("aria-live", "polite");
    expect(state).toHaveTextContent("The agent is reading your documents.");
    expect(screen.getByTestId("run-current")).toHaveTextContent(
      "Document 2 of 2: cv.txt. Reading its text.",
    );
    const figures = within(screen.getByTestId("run-figures"));
    expect(figures.getByText("1 of 2 read")).toBeVisible();
    expect(figures.getByText("3 items")).toBeVisible();
    expect(
      figures.getByText("3,760 used. The run pauses at 352,440 tokens"),
    ).toBeVisible();
    expect(figures.queryByText(/USD/)).toBeNull();
    expect(
      screen.getByText(new RegExp(OVERSHOOT.slice(0, 40))),
    ).toHaveTextContent(
      "While a step is being read the figure is behind: what the step writes is counted when it ends.",
    );
    expect(
      screen.getByRole("button", { name: "Cancel the run" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /Continue|Resume/ }),
    ).toBeNull();
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("reads the run again while it moves, and shows the summary when it ends", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { runs, state } = await show(fakeRun());
    const before = runs.sent("GET", RUN).length;
    runs.runs = [
      fakeRun({
        status: "done",
        finished_at: "2026-10-13T09:20:00Z",
        tokens: { spent: 6_580 },
        counts: { running: 0, done: 2, items: 4, proposals: 4 },
        steps: [
          fakeStep({ position: 0, status: "done", items: 3, tokens: 3_760 }),
          fakeStep({
            position: 1,
            status: "done",
            items: 1,
            tokens: 2_820,
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    ];
    // Nothing is read again before the poll is due.
    expect(runs.sent("GET", RUN)).toHaveLength(before);
    await poll();
    await waitFor(() => {
      expect(state).toHaveTextContent(
        "The run is finished. Everything you selected was read.",
      );
    });
    expect(runs.sent("GET", RUN).length).toBeGreaterThan(before);
    expect(
      screen.getByRole("heading", { name: "Your last run" }),
    ).toBeVisible();
    const figures = within(screen.getByTestId("run-figures"));
    // The summary counts the documents, not the steps.
    expect(figures.getByText("Documents")).toBeVisible();
    expect(figures.queryByText("Steps")).toBeNull();
    expect(figures.getByText("2 of 2 read")).toBeVisible();
    expect(figures.getByText("6,580 used")).toBeVisible();
    expect(figures.getByText("4 proposals to review")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel the run" })).toBeNull();
    // A new run can be started again, above the summary.
    expect(
      await screen.findByRole("heading", { name: "Start another run" }),
    ).toBeVisible();
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("paused on its ceiling: shows what was spent and what Continue adds, and sends that figure", async () => {
    const { runs, state, user } = await show(ceiling(176_220));
    expect(state).toHaveTextContent("The run has paused at its token ceiling.");
    expect(screen.getByTestId("ceiling-spent")).toHaveTextContent(
      "It has used 380,000 tokens, and its ceiling is 352,440 tokens.",
    );
    expect(screen.getByTestId("continue-adds")).toHaveTextContent(
      "Continue raises the ceiling by 176,220 tokens, to 528,660 tokens, and the run goes on. Cancel ends it here.",
    );
    expect(screen.queryByRole("button", { name: /Resume/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Cancel the run" }),
    ).toBeEnabled();
    expect(panel()).not.toHaveTextContent(RAW);
    // Nothing is sent before the click.
    expect(runs.sent("POST", `${RUN}/continue`)).toEqual([]);

    await user.click(
      screen.getByRole("button", { name: "Continue with 176,220 more tokens" }),
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    expect(runs.sent("POST", `${RUN}/continue`)).toEqual([
      { method: "POST", path: `${RUN}/continue`, body: { adds: 176_220 } },
    ]);
  });

  it("a Continue whose figure changed adds nothing: the new figure is shown and asked again", async () => {
    const { runs, state, user } = await show(ceiling(176_220));
    const button = screen.getByRole("button", {
      name: "Continue with 176,220 more tokens",
    });
    // The step that runs next changed in between: one Continue now adds more.
    runs.runs = [ceiling(352_440)];
    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Nothing was added: the figure has changed. Continue now adds 352,440 tokens. Click it again if you agree.",
    );
    // Never a success: still paused, the ceiling as it was.
    expect(state).toHaveTextContent("The run has paused at its token ceiling.");
    expect(runs.runs[0]?.status).toBe("paused");
    expect(screen.getByTestId("continue-adds")).toHaveTextContent(
      "by 352,440 tokens, to 704,880 tokens,",
    );

    await user.click(
      screen.getByRole("button", { name: "Continue with 352,440 more tokens" }),
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      runs.sent("POST", `${RUN}/continue`).map((call) => call.body),
    ).toEqual([{ adds: 176_220 }, { adds: 352_440 }]);
  });

  it("with nothing left to read it offers no Continue and says the run is closing", async () => {
    const { runs, state } = await show(ceiling(0));
    expect(state).toHaveTextContent(
      "Nothing is left to read, so the run is closing by itself.",
    );
    expect(screen.queryByRole("button", { name: /Continue/ })).toBeNull();
    expect(screen.queryByTestId("continue-adds")).toBeNull();
    expect(runs.sent("POST", `${RUN}/continue`)).toEqual([]);
  });

  it("paused on a failure: gives the server's sentence and Resume, which takes no figure", async () => {
    const reason =
      "Anthropic refused your credential. Check it on the Account page.";
    const { runs, state, user } = await show(
      fakeRun({
        status: "paused",
        paused_reason: reason,
        counts: { running: 0, planned: 1 },
      }),
    );
    expect(state).toHaveTextContent("The run is paused.");
    expect(screen.getByTestId("paused-reason")).toHaveTextContent(reason);
    expect(screen.queryByRole("button", { name: /Continue/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Resume the run" }));
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    expect(runs.sent("POST", `${RUN}/resume`)).toEqual([
      { method: "POST", path: `${RUN}/resume`, body: undefined },
    ]);
    expect(runs.sent("POST", `${RUN}/continue`)).toEqual([]);
  });

  it("Cancel ends the run from a pause on the ceiling", async () => {
    const { runs, state, user } = await show(ceiling(176_220));
    await user.click(screen.getByRole("button", { name: "Cancel the run" }));
    await waitFor(() => {
      expect(state).toHaveTextContent("The run was cancelled.");
    });
    expect(state).toHaveTextContent(
      "Nothing it read was added to your profile.",
    );
    expect(runs.sent("POST", `${RUN}/cancel`)).toHaveLength(1);
    expect(runs.sent("POST", `${RUN}/continue`)).toEqual([]);
    expect(
      screen.queryByRole("button", { name: /Continue|Cancel the run/ }),
    ).toBeNull();
  });

  it("says it is waiting for a place, and that a cancel is on its way", async () => {
    const { state } = await show(
      fakeRun({ waiting: "slots", counts: { running: 0, planned: 1 } }),
    );
    expect(state).toHaveTextContent("The run is waiting for its turn.");
    expect(state).toHaveTextContent("goes on by itself when one of them ends");
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("says a requested cancel is stopping the run", async () => {
    const { state } = await show(fakeRun({ cancel_requested: true }));
    expect(state).toHaveTextContent("Stopping the run.");
    expect(
      screen.getByRole("button", { name: "Cancel the run" }),
    ).toBeDisabled();
  });

  it("a partial run names the documents that were not read and why", async () => {
    const { state } = await show(
      fakeRun({
        status: "done",
        partial: true,
        finished_at: "2026-10-13T09:20:00Z",
        document_ids: ["d1", "d2", "d3"],
        unread_document_ids: ["d2", "d3"],
        counts: {
          steps: 3,
          running: 0,
          done: 1,
          failed: 2,
          refused: 2,
          two_digit_year: 3,
        },
        steps: [
          fakeStep({ position: 0, status: "done", items: 3, tokens: 3_760 }),
          fakeStep({
            position: 1,
            status: "failed",
            document_id: "d2",
            document_name: "cv.txt",
            error: "The step passed its token budget.",
          }),
          fakeStep({
            position: 2,
            status: "failed",
            document_id: "d3",
            document_name: null,
          }),
        ],
      }),
    );
    expect(state).toHaveTextContent(
      "The run is finished, but not everything was read.",
    );
    const unread = within(
      screen.getByRole("region", { name: "Documents that were not read" }),
    ).getAllByRole("listitem");
    expect(unread[0]).toHaveTextContent(
      "cv.txt: The step passed its token budget.",
    );
    expect(unread[1]).toHaveTextContent(
      "Deleted document: it was deleted before it was read.",
    );
    expect(
      screen.getByText(
        /Dates written with two digit years were not read \(3\)/,
      ),
    ).toBeVisible();
    expect(screen.getByText(/2 refused by the checks/)).toBeVisible();
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("a run that read nothing and failed says so", async () => {
    const { state } = await show(
      fakeRun({
        status: "failed",
        finished_at: "2026-10-13T09:20:00Z",
        unread_document_ids: ["d1", "d2"],
        counts: { running: 0, done: 0, failed: 2, items: 0 },
        steps: [
          fakeStep({ position: 0, status: "failed" }),
          fakeStep({
            position: 1,
            status: "failed",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    );
    expect(state).toHaveTextContent("The run failed. Nothing was read.");
    expect(
      within(screen.getByTestId("run-figures")).getByText("0 of 2 read"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /Continue|Resume|Cancel the run/ }),
    ).toBeNull();
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("a run the engine closed as stuck keeps what it read: its reason, never Nothing was read", async () => {
    const { state } = await show(
      fakeRun({
        status: "failed",
        partial: true,
        closed_reason: STUCK,
        finished_at: "2026-10-13T09:20:00Z",
        document_ids: ["d1", "d2", "d3"],
        unread_document_ids: ["d3"],
        counts: { steps: 3, running: 0, done: 2, cancelled: 1, items: 4 },
        steps: [
          fakeStep({ position: 0, status: "done", items: 3, tokens: 3_760 }),
          fakeStep({
            position: 1,
            status: "done",
            items: 1,
            tokens: 2_820,
            document_id: "d2",
            document_name: "cv.txt",
          }),
          fakeStep({
            position: 2,
            status: "cancelled",
            document_id: "d3",
            document_name: "letter.txt",
          }),
        ],
      }),
    );
    expect(state).toHaveAttribute("data-state", "failed");
    expect(state).toHaveTextContent("The run failed.");
    expect(state).toHaveTextContent(STUCK);
    expect(panel()).not.toHaveTextContent("Nothing was read");
    // The headline, the reason and the figures tell the same story.
    expect(
      within(screen.getByTestId("run-figures")).getByText("2 of 3 read"),
    ).toBeVisible();
    const unread = within(
      screen.getByRole("region", { name: "Documents that were not read" }),
    ).getAllByRole("listitem");
    expect(unread.map((one) => one.textContent)).toEqual([
      "letter.txt: the run ended before it was read.",
    ]);
    expect(panel()).not.toHaveTextContent(RAW);
  });

  it("lists every step with the tokens of its last attempt, and an amount only when the server gave one", async () => {
    await show(
      fakeRun({
        steps: [
          fakeStep({
            position: 0,
            status: "done",
            items: 3,
            tokens: 3_760,
            cost_usd: "0.0123",
          }),
          fakeStep({
            position: 1,
            status: "reused",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    );
    const steps = screen
      .getByText("Every step of this run")
      .closest("details") as HTMLElement;
    const [first, second] = within(steps).getAllByRole("listitem");
    expect(first).toHaveTextContent(
      "dossier.txt. Reading its text. Read, 3 items, 3,760 tokens in its last attempt, about 0.0123 USD for that attempt.",
    );
    expect(second).toHaveTextContent(
      "cv.txt. Reading its text. Already read in an earlier run, reused.",
    );
    expect(second).not.toHaveTextContent("USD");
  });

  it.each([
    ["while it runs", fakeRun({ cost_usd: "0.0312" }), "Cost so far"],
    [
      "at the end",
      fakeRun({
        status: "done",
        finished_at: "2026-10-13T09:20:00Z",
        cost_usd: "0.0412",
        counts: { running: 0, done: 2 },
        steps: [
          fakeStep({
            position: 0,
            status: "done",
            attempt: 2,
            tokens: 1_200,
            cost_usd: "0.0100",
          }),
          fakeStep({
            position: 1,
            status: "done",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
      "Cost",
    ],
  ])(
    "states the cost of the run over every attempt, as the server gives it, %s",
    async (_when, run, label) => {
      await show(run);
      const figures = within(screen.getByTestId("run-figures"));
      expect(figures.getByText(label)).toBeVisible();
      expect(
        figures.getByText(
          `about ${run.cost_usd ?? ""} USD, every attempt included`,
        ),
      ).toBeVisible();
    },
  );

  it("the summary of a run that reused every step counts its documents as read and reused", async () => {
    await show(
      fakeRun({
        status: "done",
        finished_at: "2026-10-13T09:20:00Z",
        tokens: { spent: 0, ceiling: 0 },
        counts: { running: 0, done: 0, reused: 2 },
        steps: [
          fakeStep({ position: 0, status: "reused" }),
          fakeStep({
            position: 1,
            status: "reused",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    );
    expect(
      within(screen.getByTestId("run-figures")).getByText(
        "2 of 2 read, 2 reused from an earlier run",
      ),
    ).toBeVisible();
  });

  it("a new run starts on a clean view: no message of the run before stays above it", async () => {
    const reason =
      "Anthropic refused your credential. Check it on the Account page.";
    const before = fakeRun({
      status: "paused",
      paused_reason: reason,
      counts: { running: 0, planned: 1 },
    });
    const { runs, state, user } = await show(before, {
      consent: {
        version: 1,
        text: CONSENT_TEXT,
        reminder: REMINDER,
        accepted: true,
        accepted_at: "2026-10-01T08:00:00Z",
      },
      created: fakeRun({ id: "r2" }),
    });
    // The run was cancelled in another tab: this page's Resume is refused.
    runs.runs = [
      {
        ...before,
        status: "cancelled",
        paused_reason: null,
        finished_at: "2026-10-13T09:30:00Z",
      },
    ];
    server.use(
      http.post(`${RUN}/resume`, () =>
        HttpResponse.json({ detail: "This run has ended." }, { status: 409 }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Resume the run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This run has ended.",
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The run was cancelled.");
    });

    await user.click(await screen.findByRole("checkbox", { name: /dossier/ }));
    await user.click(screen.getByRole("button", { name: "See the estimate" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Before the run starts",
    });
    await within(dialog).findByTestId("estimate");
    await user.click(
      within(dialog).getByRole("button", { name: "Start the run" }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("run-state")).toHaveTextContent(
        "The agent is reading your documents.",
      );
    });
    expect(runs.sent("POST", RUNS)).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(panel()).not.toHaveTextContent("This run has ended.");
  });

  it("a read of the run sent before a click cannot bring back the state the click left", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { runs, state, user, client } = await show(ceiling(176_220));
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    // The next read answers what the server had when it was asked: the pause.
    server.use(
      http.get(
        RUN,
        async () => {
          const asked = runs.runs[0];
          await held;
          return HttpResponse.json(asked);
        },
        { once: true },
      ),
    );
    await poll();
    expect(client.getQueryState(runKey("r1"))?.fetchStatus).toBe("fetching");
    await user.click(
      screen.getByRole("button", { name: "Continue with 176,220 more tokens" }),
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    release();
    await waitFor(() => {
      expect(client.getQueryState(runKey("r1"))?.fetchStatus).toBe("idle");
    });
    expect(state).toHaveTextContent("The agent is reading your documents.");
    expect(screen.queryByRole("button", { name: /Continue/ })).toBeNull();
    expect(runs.sent("POST", `${RUN}/continue`)).toHaveLength(1);
  });

  it("the step's panel offers no Cancel of its own: the run's Cancel is the only one", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const agentRun = "33333333-3333-4333-8333-333333333333";
    server.use(
      http.get(`/api/agents/runs/${agentRun}`, () =>
        HttpResponse.json({
          id: agentRun,
          agent: "ingestion_extract",
          job_id: null,
          status: "running",
          input: {},
          output: null,
          error: null,
          model: "claude-opus-5-5",
          input_tokens: null,
          output_tokens: null,
          cost_usd: null,
          cancel_requested: false,
          created_at: "2026-10-13T09:00:00Z",
          started_at: "2026-10-13T09:00:01Z",
          finished_at: null,
          duration_s: null,
          events: [],
          events_truncated: false,
        }),
      ),
    );
    const { user } = await show(fakeRun({ current_agent_run_id: agentRun }));
    await user.click(screen.getByText("What the agent is doing now"));
    const agent = await screen.findByTestId("run-panel");
    expect(agent).toHaveAttribute("data-status", "running");
    expect(within(agent).queryByRole("button", { name: /Cancel/ })).toBeNull();
    expect(
      screen
        .getAllByRole("button", { name: /Cancel/ })
        .map((one) => one.textContent),
    ).toEqual(["Cancel the run"]);
  });

  it("keeps the run on screen when the server cannot be reached, says it keeps trying, then recovers", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { state } = await show(fakeRun());
    server.use(http.get(RUN, () => HttpResponse.error(), { once: true }));
    await poll();
    expect(
      await screen.findByText(
        "The server could not be reached just now. This page keeps trying.",
      ),
    ).toBeVisible();
    expect(state).toHaveTextContent("The agent is reading your documents.");
    expect(
      screen.getByRole("button", { name: "Cancel the run" }),
    ).toBeEnabled();
    await poll();
    await waitFor(() => {
      expect(screen.queryByText(/This page keeps trying/)).toBeNull();
    });
    expect(state).toHaveTextContent("The agent is reading your documents.");
  });

  it("keyboard only: Tab reaches Continue then Cancel, and Enter sends the figure shown", async () => {
    const { runs, state, user } = await show(ceiling(176_220));
    const go = screen.getByRole("button", {
      name: "Continue with 176,220 more tokens",
    });
    for (let press = 0; press < 60 && document.activeElement !== go; press++)
      await user.tab();
    expect(go).toHaveFocus();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Cancel the run" }),
    ).toHaveFocus();
    await user.tab({ shift: true });
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    expect(
      runs.sent("POST", `${RUN}/continue`).map((call) => call.body),
    ).toEqual([{ adds: 176_220 }]);
    expect(runs.sent("POST", `${RUN}/cancel`)).toEqual([]);
  });

  it("the tokens spent move up at the end of a step, by more than its last request", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { runs } = await show(
      fakeRun({
        tokens: { spent: 1_200 },
        counts: { running: 1, planned: 1, done: 0, items: 0 },
        steps: [
          fakeStep({ position: 0, status: "running", tokens: 1_200 }),
          fakeStep({ position: 1, document_id: "d2", document_name: "cv.txt" }),
        ],
      }),
    );
    const figures = () => within(screen.getByTestId("run-figures"));
    expect(
      figures().getByText("1,200 used. The run pauses at 352,440 tokens"),
    ).toBeVisible();
    // The step's output is counted with its result: the figure jumps when it ends.
    runs.runs = [
      fakeRun({
        tokens: { spent: 5_000 },
        counts: { running: 1, planned: 0, done: 1, items: 2 },
        steps: [
          fakeStep({ position: 0, status: "done", items: 2, tokens: 5_000 }),
          fakeStep({
            position: 1,
            status: "running",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    ];
    await poll();
    await waitFor(() => {
      expect(
        figures().getByText("5,000 used. The run pauses at 352,440 tokens"),
      ).toBeVisible();
    });
    expect(screen.getByTestId("run-current")).toHaveTextContent(
      "Document 2 of 2: cv.txt.",
    );
    expect(
      screen.getByText(/While a step is being read the figure is behind/),
    ).toBeVisible();
  });

  it("paused on its ceiling, continued twice, then cancelled", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { runs, state, user } = await show(ceiling(176_220));
    const pauseAgain = (spent: number, at: number, adds: number) => {
      runs.runs = [
        ceiling(adds, {
          tokens: { spent, ceiling: at, continue_adds: adds },
        }),
      ];
    };

    await user.click(
      screen.getByRole("button", { name: "Continue with 176,220 more tokens" }),
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    pauseAgain(540_000, 528_660, 352_440);
    await poll();
    await waitFor(() => {
      expect(state).toHaveAttribute("data-state", "ceiling");
    });
    expect(screen.getByTestId("ceiling-spent")).toHaveTextContent(
      "It has used 540,000 tokens, and its ceiling is 528,660 tokens.",
    );
    expect(screen.getByTestId("continue-adds")).toHaveTextContent(
      "by 352,440 tokens, to 881,100 tokens,",
    );

    await user.click(
      screen.getByRole("button", { name: "Continue with 352,440 more tokens" }),
    );
    await waitFor(() => {
      expect(state).toHaveTextContent("The agent is reading your documents.");
    });
    pauseAgain(900_000, 881_100, 352_440);
    await poll();
    await waitFor(() => {
      expect(state).toHaveAttribute("data-state", "ceiling");
    });

    await user.click(screen.getByRole("button", { name: "Cancel the run" }));
    await waitFor(() => {
      expect(state).toHaveTextContent("The run was cancelled.");
    });
    expect(
      runs.sent("POST", `${RUN}/continue`).map((call) => call.body),
    ).toEqual([{ adds: 176_220 }, { adds: 352_440 }]);
    expect(runs.sent("POST", `${RUN}/cancel`)).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: /Continue|Cancel the run/ }),
    ).toBeNull();
  });
});
