import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import {
  CONSENT_TEXT,
  fakeEstimate,
  fakeKeyEstimate,
  fakeRun,
  fakeRuns,
  QUOTE_A,
  QUOTE_B,
  REMINDER,
  type FakeRuns,
} from "@/test/ingestion-runs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { IDLE_POLL_MS } from "./runs-api";

const ESTIMATE = "/api/ingestion/runs/estimate";
const CONSENT = "/api/ingestion/consent";
const RUNS = "/api/ingestion/runs";
const QUOTE_C = "c".repeat(64);
const SELECTION = { document_ids: ["d1", "d2"], reread_document_ids: [] };
const DASH = /[–—]| - /;

const DOCUMENTS = [
  fakeDocument({ id: "d1", name: "dossier.txt" }),
  fakeDocument({ id: "d2", name: "cv.txt" }),
];

async function openStep(initial: Partial<FakeRuns> = {}) {
  fakeSession(member);
  fakeDocuments(DOCUMENTS);
  fakePrompt();
  const runs = fakeRuns(initial);
  const app = renderApp("/profile/fill?step=review");
  await screen.findByRole("heading", {
    level: 2,
    name: "Step 3: Review what the agent proposes",
  });
  return { app, runs, user: userEvent.setup() };
}

/** Selects both documents and opens the start dialog on its estimate. */
async function openDialog(initial: Partial<FakeRuns> = {}) {
  const opened = await openStep(initial);
  const { user } = opened;
  await user.click(await screen.findByRole("checkbox", { name: /dossier/ }));
  await user.click(screen.getByRole("checkbox", { name: /cv\.txt/ }));
  await user.click(screen.getByRole("button", { name: "See the estimate" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Before the run starts",
  });
  await within(dialog).findByTestId("estimate");
  return { ...opened, dialog };
}

const startButton = (dialog: HTMLElement) =>
  within(dialog).getByRole("button", { name: "Start the run" });
const accepted = {
  version: 1,
  text: CONSENT_TEXT,
  reminder: REMINDER,
  accepted: true,
  accepted_at: "2026-10-01T08:00:00Z",
};

afterEach(() => {
  vi.useRealTimers();
});

describe("the start of a run", () => {
  it("sends nothing until a document is chosen and the estimate is asked for", async () => {
    const { runs, user } = await openStep();
    const see = await screen.findByRole("button", { name: "See the estimate" });
    expect(see).toBeDisabled();
    expect(runs.sent("POST", ESTIMATE)).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Select all" }));
    expect(see).toBeEnabled();
    expect(screen.getByText("2 of 2 selected")).toBeVisible();
    expect(runs.sent("POST", ESTIMATE)).toEqual([]);
    expect(runs.sent("POST", RUNS)).toEqual([]);
  });

  it("asks for the documents in the order they were ticked", async () => {
    const { runs, user } = await openStep();
    await user.click(await screen.findByRole("checkbox", { name: /cv\.txt/ }));
    await user.click(screen.getByRole("checkbox", { name: /dossier/ }));
    await user.click(screen.getByRole("button", { name: "See the estimate" }));
    await waitFor(() => {
      expect(runs.sent("POST", ESTIMATE).map((call) => call.body)).toEqual([
        { document_ids: ["d2", "d1"], reread_document_ids: [] },
      ]);
    });
  });

  it("shows the estimate of a subscription as the server gave it, in tokens and no amount", async () => {
    const { runs, dialog } = await openDialog();
    expect(runs.sent("POST", ESTIMATE)).toEqual([
      { method: "POST", path: ESTIMATE, body: SELECTION },
    ]);
    const shown = within(dialog);
    expect(shown.getByTestId("estimate-range")).toHaveTextContent(
      "About 61,234 to 176,220 tokens.",
    );
    expect(shown.getByTestId("estimate-ceiling")).toHaveTextContent(
      "The run pauses at 352,440 tokens, and asks you before going on.",
    );
    expect(shown.getByTestId("estimate-model")).toHaveTextContent(
      "so the run uses claude-opus-5-5.",
    );
    expect(shown.getByTestId("estimate-quota")).toHaveTextContent(
      "tokens count against your quota, not as money.",
    );
    expect(shown.getByText(/12,345 characters, 1 step/)).toBeVisible();
    expect(shown.getByText(/Limits of a run: 30 documents/)).toBeVisible();
    expect(dialog).not.toHaveTextContent("USD");
    expect(dialog).not.toHaveTextContent(DASH);
  });

  it("asks for the consent the first time: no start until the box is ticked", async () => {
    const { runs, user, dialog } = await openDialog();
    const shown = within(dialog);
    expect(await shown.findByTestId("consent-text")).toHaveTextContent(
      CONSENT_TEXT,
    );
    const box = shown.getByRole("checkbox", { name: /I have read this/ });
    expect(box).not.toBeChecked();
    expect(startButton(dialog)).toBeDisabled();
    await user.click(box);
    expect(startButton(dialog)).toBeEnabled();
    // Ticking is not consenting: nothing is recorded before the click on Start.
    expect(runs.sent("POST", CONSENT)).toEqual([]);

    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(runs.sent("POST", CONSENT)).toEqual([
      { method: "POST", path: CONSENT, body: { version: 1 } },
    ]);
    // The start echoes the quote_id of the estimate shown, with the same selection.
    expect(runs.sent("POST", RUNS)).toEqual([
      { method: "POST", path: RUNS, body: { ...SELECTION, quote_id: QUOTE_A } },
    ]);
    const order = runs.calls
      .filter((call) => call.method === "POST")
      .map((call) => call.path);
    expect(order).toEqual([ESTIMATE, CONSENT, RUNS]);
    // The page now shows the run, and the start steps aside.
    expect(
      await screen.findByRole("heading", { name: "The run in progress" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "See the estimate" }),
    ).toBeNull();
  });

  it("declined: closing the dialog records no consent and starts nothing", async () => {
    const { runs, user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runs.sent("POST", CONSENT)).toEqual([]);
    expect(runs.sent("POST", RUNS)).toEqual([]);
    expect(runs.runs).toEqual([]);
    // The focus is back on the button that opened the dialog.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "See the estimate" }),
      ).toHaveFocus();
    });
  });

  it("shows the one line reminder once the consent is recorded, and asks nothing again", async () => {
    const { runs, user, dialog } = await openDialog({ consent: accepted });
    const shown = within(dialog);
    expect(await shown.findByTestId("reminder")).toHaveTextContent(REMINDER);
    expect(shown.queryByRole("checkbox", { name: /I have read/ })).toBeNull();
    expect(shown.queryByTestId("consent-text")).toBeNull();
    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(runs.sent("POST", RUNS)).toHaveLength(1);
    });
    expect(runs.sent("POST", CONSENT)).toEqual([]);
  });

  it("shows the three amounts of an API key as estimates with their date, never as a maximum", async () => {
    const { dialog } = await openDialog({ estimate: fakeKeyEstimate() });
    const shown = within(dialog);
    expect(shown.getByTestId("estimate-cost")).toHaveTextContent(
      "About 0.21 to 1.70 USD, as an estimate.",
    );
    expect(shown.getByTestId("estimate-cost-ceiling")).toHaveTextContent(
      "The pause at 352,440 tokens is estimated at about 3.40 USD. It is an estimate, not a limit on what you pay.",
    );
    expect(
      shown.getByText(/Estimates from list prices read on 2026-10-01/),
    ).toBeVisible();
    expect(shown.getByTestId("estimate-model")).toHaveTextContent(
      "claude-sonnet-5-5",
    );
    expect(shown.queryByTestId("estimate-quota")).toBeNull();
    expect(dialog).not.toHaveTextContent(/at most|maximum|up to/i);
  });

  it("a start whose estimate changed starts nothing, shows the new figures and waits for another click", async () => {
    const { runs, user, dialog } = await openDialog({ consent: accepted });
    const shown = within(dialog);
    expect(dialog).not.toHaveTextContent("USD");
    // The credential is replaced in another tab between the dialog and the click.
    runs.estimate = fakeKeyEstimate({
      tokens: { low: 50_000, high: 150_000, ceiling: 300_000 },
    });
    await user.click(startButton(dialog));

    expect(await shown.findByRole("alert")).toHaveTextContent(
      "The estimate has changed since you opened it. Nothing was started.",
    );
    expect(runs.runs).toEqual([]);
    expect(
      screen.queryByRole("heading", { name: "The run in progress" }),
    ).toBeNull();
    expect(shown.getByTestId("estimate-range")).toHaveTextContent(
      "About 50,000 to 150,000 tokens.",
    );
    expect(shown.getByTestId("estimate-ceiling")).toHaveTextContent("300,000");
    expect(shown.getByTestId("estimate-cost")).toHaveTextContent(
      "0.21 to 1.70",
    );
    expect(shown.getByTestId("estimate-model")).toHaveTextContent(
      "claude-sonnet-5-5",
    );

    // A second change before the second click: refused again, shown again.
    runs.estimate = fakeEstimate({ quote_id: QUOTE_C });
    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(shown.getByTestId("estimate-range")).toHaveTextContent("61,234");
    });
    expect(dialog).not.toHaveTextContent("USD");
    expect(runs.runs).toEqual([]);

    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(runs.runs).toHaveLength(1);
    });
    expect(
      runs
        .sent("POST", RUNS)
        .map((call) => (call.body as { quote_id: string }).quote_id),
    ).toEqual([QUOTE_A, QUOTE_B, QUOTE_C]);
    for (const call of runs.sent("POST", RUNS))
      expect(call.body).toMatchObject(SELECTION);
  });

  it("a new consent text asks again: the start is refused and the box comes back unticked", async () => {
    const { runs, user, dialog } = await openDialog({ consent: accepted });
    const shown = within(dialog);
    await shown.findByTestId("reminder");
    // A new version is deployed: the recorded consent no longer counts.
    runs.consent = {
      version: 2,
      text: "The new words of the consent.",
      reminder: REMINDER,
      accepted: false,
      accepted_at: null,
    };
    await user.click(startButton(dialog));
    expect(await shown.findByTestId("consent-text")).toHaveTextContent(
      "The new words of the consent.",
    );
    expect(runs.runs).toEqual([]);
    expect(startButton(dialog)).toBeDisabled();
    await user.click(shown.getByRole("checkbox", { name: /I have read this/ }));
    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(runs.runs).toHaveLength(1);
    });
    expect(runs.sent("POST", CONSENT).map((call) => call.body)).toEqual([
      { version: 2 },
    ]);
  });

  it("a new consent text under a ticked box: the tick does not carry over, nothing is recorded for the new words", async () => {
    const { runs, user, dialog } = await openDialog();
    const shown = within(dialog);
    await user.click(
      await shown.findByRole("checkbox", { name: /I have read this/ }),
    );
    expect(startButton(dialog)).toBeEnabled();
    // The text changes after the tick; the click on Start still carries version 1.
    runs.consent = {
      version: 2,
      text: "The new words of the consent.",
      reminder: REMINDER,
      accepted: false,
      accepted_at: null,
    };
    await user.click(startButton(dialog));
    expect(await shown.findByTestId("consent-text")).toHaveTextContent(
      "The new words of the consent.",
    );
    expect(
      shown.getByRole("checkbox", { name: /I have read this/ }),
    ).not.toBeChecked();
    expect(startButton(dialog)).toBeDisabled();
    expect(shown.getByTestId("consent-changed")).toHaveTextContent(
      "The text changed after you ticked the box.",
    );
    expect(shown.getByTestId("consent-changed").textContent).not.toMatch(DASH);
    expect(runs.sent("POST", CONSENT).map((call) => call.body)).toEqual([
      { version: 1 },
    ]);
    expect(runs.sent("POST", RUNS)).toEqual([]);
    expect(runs.runs).toEqual([]);
  });

  it("a refetch that brings a new consent text unticks the box and disables the start", async () => {
    const { app, runs, user, dialog } = await openDialog();
    const shown = within(dialog);
    await user.click(
      await shown.findByRole("checkbox", { name: /I have read this/ }),
    );
    runs.consent = {
      version: 2,
      text: "The new words of the consent.",
      reminder: REMINDER,
      accepted: false,
      accepted_at: null,
    };
    // Something else refetches the consent while the box is ticked.
    await app.client.invalidateQueries({ queryKey: ["ingestion", "consent"] });
    expect(await shown.findByTestId("consent-text")).toHaveTextContent(
      "The new words of the consent.",
    );
    expect(
      shown.getByRole("checkbox", { name: /I have read this/ }),
    ).not.toBeChecked();
    expect(startButton(dialog)).toBeDisabled();
    expect(shown.getByTestId("consent-changed")).toBeVisible();
    expect(runs.sent("POST", CONSENT)).toEqual([]);
    expect(runs.sent("POST", RUNS)).toEqual([]);
    // Ticking again under the new words is what enables the start.
    await user.click(shown.getByRole("checkbox", { name: /I have read this/ }));
    expect(startButton(dialog)).toBeEnabled();
    expect(shown.queryByTestId("consent-changed")).toBeNull();
  });

  it("says what is missing without a credential, with a link to the account page", async () => {
    const { dialog } = await openDialog({
      estimate: fakeEstimate({
        credential_kind: null,
        model: null,
        model_reason: "Save a credential first.",
        cost_note: null,
        can_start: false,
        blockers: [
          {
            code: "no_credential",
            reason: "Save a Claude subscription token or an API key first.",
          },
        ],
      }),
    });
    const shown = within(dialog);
    expect(
      shown.getByText(/Save a Claude subscription token or an API key first/),
    ).toBeVisible();
    expect(
      shown.getByRole("link", { name: "Open your account page" }),
    ).toHaveAttribute("href", "/account");
    await shown.findByTestId("consent-text");
    expect(startButton(dialog)).toBeDisabled();
  });

  it("shows why a document is not read next to that document", async () => {
    const { dialog } = await openDialog({
      estimate: fakeEstimate({
        documents: [fakeEstimate().documents[0]!],
        unreadable: [
          {
            id: "d2",
            name: "cv.txt",
            code: "empty",
            reason: "This document has no text to read.",
          },
        ],
        can_start: false,
        blockers: [
          {
            code: "unreadable_documents",
            reason: "Remove the documents that cannot be read.",
          },
        ],
      }),
    });
    const row = within(dialog)
      .getAllByRole("listitem")
      .find((item) => item.textContent.includes("cv.txt"));
    expect(row).toHaveTextContent(
      "Not read: This document has no text to read.",
    );
    expect(startButton(dialog)).toBeDisabled();
    expect(dialog).not.toHaveTextContent(/unreadable_documents|empty\b/);
  });

  it("marks a document already read as reused and offers to read it again, with its warning", async () => {
    const reused = fakeEstimate({
      documents: fakeEstimate().documents.map((one) => ({
        ...one,
        reused_steps: one.id === "d1" ? 1 : 0,
      })),
      reused_steps: 1,
    });
    const { runs, user, dialog } = await openDialog({
      estimate: reused,
      consent: accepted,
    });
    const shown = within(dialog);
    expect(shown.getByText(/1 already read and reused$/)).toBeVisible();
    const again = shown.getByRole("checkbox", {
      name: /Read dossier\.txt again/,
    });
    expect(again).not.toBeChecked();
    expect(
      shown.getByText("Reading it again uses tokens again."),
    ).toBeVisible();
    expect(
      shown.queryByRole("checkbox", { name: /Read cv\.txt again/ }),
    ).toBeNull();

    runs.estimate = fakeEstimate({ quote_id: QUOTE_B });
    await user.click(again);
    await waitFor(() => {
      expect(runs.sent("POST", ESTIMATE).map((call) => call.body)).toEqual([
        SELECTION,
        { document_ids: ["d1", "d2"], reread_document_ids: ["d1"] },
      ]);
    });
    await waitFor(() => {
      expect(startButton(dialog)).toBeEnabled();
    });
    await user.click(startButton(dialog));
    await waitFor(() => {
      expect(runs.runs).toHaveLength(1);
    });
    expect(runs.sent("POST", RUNS)[0]?.body).toEqual({
      document_ids: ["d1", "d2"],
      reread_document_ids: ["d1"],
      quote_id: QUOTE_B,
    });
  });

  it("a run already active elsewhere: the start is refused and the page opens that run", async () => {
    const { runs, user, dialog } = await openDialog({ consent: accepted });
    const other = fakeRun({ id: "r9" });
    runs.startAnswers.push(() => {
      runs.runs.unshift(other);
      return HttpResponse.json(
        {
          detail: "A run is already in progress.",
          code: "active_run",
          run_id: "r9",
        },
        { status: 409 },
      );
    });
    await user.click(startButton(dialog));
    expect(
      await screen.findByRole("heading", { name: "The run in progress" }),
    ).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(runs.sent("GET", "/api/ingestion/runs/r9")).not.toHaveLength(0);
    });
  });

  it("steps aside while a run is active: the page shows that run and no start", async () => {
    await openStep({ runs: [fakeRun()] });
    expect(
      await screen.findByRole("heading", { name: "The run in progress" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "See the estimate" }),
    ).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("a document that cannot be read yet cannot be chosen", async () => {
    fakeSession(member);
    fakeDocuments([
      ...DOCUMENTS,
      fakeDocument({
        id: "d3",
        name: "scan.pdf",
        kind: "pdf",
        status: "pending",
      }),
    ]);
    fakePrompt();
    fakeRuns();
    renderApp("/profile/fill?step=review");
    expect(
      await screen.findByRole("checkbox", { name: /scan\.pdf/ }),
    ).toBeDisabled();
    expect(screen.getByText(/it cannot be read yet/)).toBeVisible();
  });

  it("a document whose page images wait for the agent can be chosen", async () => {
    fakeSession(member);
    fakeDocuments([
      ...DOCUMENTS,
      fakeDocument({
        id: "d3",
        name: "scan.pdf",
        kind: "pdf",
        status: "awaiting_transcription",
      }),
    ]);
    fakePrompt();
    fakeRuns();
    renderApp("/profile/fill?step=review");
    const box = await screen.findByRole("checkbox", { name: /scan\.pdf/ });
    expect(box).toBeEnabled();
    expect(screen.queryByText(/it cannot be read yet/)).toBeNull();
    await userEvent.click(box);
    expect(box).toBeChecked();
    expect(
      screen.getByRole("button", { name: "See the estimate" }),
    ).toBeEnabled();
  });

  it.each([
    "A run reads at most 30 documents, and 31 are selected. Select fewer and start again for the others.",
    "A run reads at most 1,500,000 characters, and the selection holds 1,612,345. Select fewer documents and start again for the others.",
    "A run reads at most 60 page images, and the selection holds 61. Select fewer documents and start again for the others.",
  ])(
    "a selection over a limit: the dialog says what to change and starts nothing (%s)",
    async (sentence) => {
      const { runs, user } = await openStep();
      server.use(
        http.post(ESTIMATE, () =>
          HttpResponse.json({ detail: sentence }, { status: 422 }),
        ),
      );
      await user.click(
        await screen.findByRole("button", { name: "Select all" }),
      );
      await user.click(
        screen.getByRole("button", { name: "See the estimate" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Before the run starts",
      });
      expect(await within(dialog).findByText(sentence)).toBeVisible();
      expect(within(dialog).queryByTestId("estimate")).toBeNull();
      expect(startButton(dialog)).toBeDisabled();
      expect(dialog).not.toHaveTextContent(DASH);
      expect(runs.sent("POST", RUNS)).toEqual([]);
      expect(runs.sent("POST", CONSENT)).toEqual([]);
    },
  );

  it("a first read of the runs that fails says this step could not be loaded", async () => {
    fakeSession(member);
    fakeDocuments(DOCUMENTS);
    fakePrompt();
    fakeRuns();
    server.use(
      http.get(RUNS, () =>
        HttpResponse.json({ detail: "Unavailable." }, { status: 503 }),
      ),
    );
    renderApp("/profile/fill?step=review");
    expect(
      await screen.findByText("This step could not be loaded"),
    ).toBeVisible();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("a background read of the runs that fails keeps the open dialog and says the page keeps trying", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { runs, dialog, user } = await openDialog();
    server.use(
      http.get(RUNS, () =>
        HttpResponse.json({ detail: "Unavailable." }, { status: 503 }),
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_POLL_MS);
    });
    expect(
      await screen.findByText(
        "The server could not be reached just now. This page keeps trying.",
      ),
    ).toBeInTheDocument();
    // The dialog, its estimate and its box are the ones the user was reading.
    expect(dialog).toBeVisible();
    expect(within(dialog).getByTestId("estimate")).toBeVisible();
    expect(screen.queryByText("This step could not be loaded")).toBeNull();
    expect(runs.sent("POST", ESTIMATE)).toHaveLength(1);
    // The next read succeeds: the notice goes, and the start still works.
    server.resetHandlers();
    fakeSession(member);
    fakeDocuments(DOCUMENTS);
    fakePrompt();
    const again = fakeRuns();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_POLL_MS);
    });
    await waitFor(() => {
      expect(screen.queryByText(/This page keeps trying/)).toBeNull();
    });
    expect(again.sent("GET", RUNS)).not.toHaveLength(0);
    await user.click(
      within(dialog).getByRole("checkbox", { name: /I have read this/ }),
    );
    expect(startButton(dialog)).toBeEnabled();
  });
});
