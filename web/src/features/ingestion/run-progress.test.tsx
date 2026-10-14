import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import {
  fakeRun,
  fakeRuns,
  fakeStep,
  OVERSHOOT,
  type FakeRuns,
} from "@/test/ingestion-runs";
import { renderApp } from "@/test/render";

import type { Run } from "./runs-api";

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
  renderApp("/profile/fill?step=review");
  const state = await screen.findByTestId("run-state");
  return { runs, state, user: userEvent.setup() };
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

  it(
    "reads the run again while it moves, and shows the summary when it ends",
    { timeout: 15_000 },
    async () => {
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
      await waitFor(
        () => {
          expect(state).toHaveTextContent(
            "The run is finished. Everything you selected was read.",
          );
        },
        { timeout: 5_000 },
      );
      expect(runs.sent("GET", RUN).length).toBeGreaterThan(before);
      expect(
        screen.getByRole("heading", { name: "Your last run" }),
      ).toBeVisible();
      const figures = within(screen.getByTestId("run-figures"));
      expect(figures.getByText("2 of 2 read")).toBeVisible();
      expect(figures.getByText("6,580 used")).toBeVisible();
      expect(figures.getByText("4 proposals to review")).toBeVisible();
      expect(
        screen.queryByRole("button", { name: "Cancel the run" }),
      ).toBeNull();
      // A new run can be started again, above the summary.
      expect(
        await screen.findByRole("heading", { name: "Start another run" }),
      ).toBeVisible();
      expect(panel()).not.toHaveTextContent(RAW);
    },
  );

  it("paused on its ceiling: shows what was spent and what Continue adds, and sends that figure", async () => {
    const { runs, state, user } = await show(ceiling(176_220));
    expect(state).toHaveTextContent("The run has paused at its token ceiling.");
    expect(screen.getByTestId("ceiling-spent")).toHaveTextContent(
      "It has used 380,000 tokens, and its ceiling is 352,440 tokens.",
    );
    expect(screen.getByTestId("continue-adds")).toHaveTextContent(
      "Continue raises the ceiling by 176,220 tokens",
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
    expect(screen.getByTestId("continue-adds")).toHaveTextContent("352,440");

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

  it("a failed run says so, with the reason the engine closed it", async () => {
    const closed =
      "The run could not go on and was closed. Start a new one: what was read is reused.";
    const { state } = await show(
      fakeRun({
        status: "failed",
        partial: true,
        closed_reason: closed,
        finished_at: "2026-10-13T09:20:00Z",
        unread_document_ids: ["d1", "d2"],
        counts: { running: 0, done: 0, cancelled: 2, items: 0 },
        steps: [
          fakeStep({ position: 0, status: "cancelled" }),
          fakeStep({
            position: 1,
            status: "cancelled",
            document_id: "d2",
            document_name: "cv.txt",
          }),
        ],
      }),
    );
    expect(state).toHaveTextContent("The run failed. Nothing was read.");
    expect(state).toHaveTextContent(closed);
    expect(
      screen.queryByRole("button", { name: /Continue|Resume|Cancel the run/ }),
    ).toBeNull();
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
});
