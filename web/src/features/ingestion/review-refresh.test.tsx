import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments, fakePrompt } from "@/test/ingestion";
import { fakeRun, fakeRuns, fakeStep } from "@/test/ingestion-runs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Run } from "./runs-api";

const CV = { id: "d1", name: "dossier.txt", deleted: false };

/** What the proposals route answers for one run: one pending item per key. */
function review(runId: string, keys: string[]) {
  const items = keys.map((key) => ({
    id: `${runId}-${key}`,
    status: "pending",
    section: "identity",
    key,
    slot: key,
    action: "add",
    value: key,
    before: null,
    after: key,
    noop: false,
    stale: false,
    stale_reason: null,
    locked: false,
    bulk: null,
    problem: null,
    flags: [],
    free_text: false,
    citations: [
      {
        document_id: CV.id,
        document: CV,
        excerpt: "Alice Martin",
        excerpt_cut: false,
        start: 0,
        end: 12,
        transcribed: false,
      },
    ],
    conflict: [],
    depends_on: [],
    base_revision: 1,
    applied_revision: null,
  }));
  return {
    run_id: runId,
    status: "done",
    partial: false,
    profile_revision: 1,
    counts: {
      total: items.length,
      pending: items.length,
      accepted: 0,
      rejected: 0,
      superseded: 0,
      invalid: 0,
      undone: 0,
    },
    documents: [CV],
    sections: [
      {
        section: "identity",
        counts: {
          total: items.length,
          pending: items.length,
          accepted: 0,
          rejected: 0,
          superseded: 0,
          invalid: 0,
          undone: 0,
        },
        items,
      },
    ],
  };
}

const PROPOSED: Record<string, string[]> = {
  r1: ["full_name"],
  r2: ["headline", "location"],
};

/** A run stopped on its ceiling: its Continue is the click that ends it in these tests. */
const stopped = (id: string) =>
  fakeRun({
    id,
    status: "paused",
    paused_reason: "token_ceiling",
    tokens: { spent: 380_000, ceiling: 352_440, continue_adds: 176_220 },
    counts: { running: 0, planned: 1 },
    steps: [
      fakeStep({ position: 0, status: "done", items: 1, tokens: 380_000 }),
      fakeStep({
        position: 1,
        document_id: "d1",
        document_name: "dossier.txt",
      }),
    ],
  });

const finished = (id: string, over: Partial<Run> = {}) =>
  fakeRun({
    id,
    status: "done",
    finished_at: "2026-10-13T09:20:00Z",
    counts: { running: 0, done: 2, items: 4, proposals: 1 },
    ...over,
  });

async function open(runs: Run[]) {
  fakeSession(member);
  fakeDocuments([fakeDocument({ id: "d1", name: "dossier.txt" })]);
  fakePrompt();
  fakeRuns({
    runs,
    // The Continue of the page ends the run, as the server does once its steps are read.
    afterAction: (run) => ({
      ...run,
      status: "done",
      paused_reason: null,
      finished_at: "2026-10-13T09:40:00Z",
      tokens: { ...run.tokens, continue_adds: null },
    }),
  });
  server.use(
    http.get("/api/ingestion/runs/:id/proposals", ({ params }) => {
      const id = String(params.id);
      const keys = PROPOSED[id];
      return keys
        ? HttpResponse.json(review(id, keys))
        : HttpResponse.json({ detail: "Run not found." }, { status: 404 });
    }),
  );
  renderApp("/profile/fill?step=review");
  await screen.findByTestId("run-state");
  return userEvent.setup();
}

const keys = () =>
  screen.queryAllByTestId("proposal").map((one) => one.dataset.key);

const CONTINUE = { name: "Continue with 176,220 more tokens" };

describe("the proposals of the review step follow the runs of an open page", () => {
  it("shows the proposals of the run that just finished, with no reload", async () => {
    const user = await open([stopped("r1")]);
    // No run has finished: nothing is proposed.
    expect(keys()).toEqual([]);
    await user.click(screen.getByRole("button", CONTINUE));
    await waitFor(() => {
      expect(keys()).toEqual(["full_name"]);
    });
  });

  it("replaces the list of the first run with the one of the second", async () => {
    const user = await open([stopped("r2"), finished("r1")]);
    await waitFor(() => {
      expect(keys()).toEqual(["full_name"]);
    });
    await user.click(screen.getByRole("button", CONTINUE));
    await waitFor(() => {
      expect(keys()).toEqual(["headline", "location"]);
    });
  });
});
