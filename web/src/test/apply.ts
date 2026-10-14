import { http, HttpResponse } from "msw";

import type { Answer, Application } from "@/features/apply/apply-api";

import { server } from "./server";

export const APP_ID = "00000000-0000-4000-9000-0000000000a1";

export function fakeApplication(
  state: string,
  overrides: Partial<Application> = {},
): Application {
  return {
    id: APP_ID,
    posting_id: "00000000-0000-4000-9000-000000000001",
    mode: "review",
    dry_run: false,
    state,
    ats: "greenhouse",
    form_url: "https://boards.example.org/robotics/jobs/1",
    attempt: 1,
    needs: null,
    error_code: null,
    created_at: "2026-10-15T08:00:00Z",
    updated_at: "2026-10-15T08:01:12Z",
    submitted_at: null,
    ...overrides,
  };
}

export function fakeAnswer(
  key: string,
  label: string,
  value: unknown,
  overrides: Partial<Answer> = {},
): Answer {
  return {
    field_key: key,
    label,
    kind: "text",
    value,
    source: "identity",
    required: false,
    approved: false,
    you_answer: false,
    ...overrides,
  };
}

export const leftAnswer = (
  key: string,
  label: string,
  overrides: Partial<Answer> = {},
) =>
  fakeAnswer(key, label, null, {
    source: "left",
    you_answer: true,
    ...overrides,
  });

/**
 * A fake of the apply routes the Apply section calls, in memory. `application` and `answers` are
 * changed by a test to move the run on; every write is recorded. An answer saved through PUT
 * becomes the person's and changes the hash, as the api does.
 */
export function fakeApply(initial: {
  application: Application | null;
  answers?: Answer[];
}) {
  const state = {
    application: initial.application,
    answers: initial.answers ?? [],
    version: 1,
    documents: {
      cv: {
        version_id: "v-cv",
        number: 3,
        fit: null,
        pages: 1,
        corrections: 0,
      },
      letter: {
        version_id: "v-letter",
        number: 2,
        fit: null,
        pages: 1,
        corrections: 0,
      },
    } as Record<string, unknown>,
    screenshots: [
      { id: "shot-before", kind: "before", created_at: "2026-10-15T08:01:00Z" },
    ] as { id: string; kind: string; created_at: string }[],
    events: [
      { at: "2026-10-15T08:00:00Z", kind: "created", data: {} },
      { at: "2026-10-15T08:01:00Z", kind: "filled", data: {} },
    ] as { at: string; kind: string; data: Record<string, unknown> }[],
    started: 0,
    startRefusal: null as { detail: string; code: string } | null,
    puts: [] as { field: string; body: unknown }[],
    approves: [] as string[],
    decisions: [] as string[],
    /** How many times the approval was read, and a gate that holds its answer while set. */
    reads: 0,
    hold: null as Promise<void> | null,
    /** The next decision (reject, cancel, submitted, retry) answers 409 with this refusal. */
    decisionRefusal: null as {
      detail: string;
      code: string;
      state?: string;
    } | null,
    /** The next approve answers 409 stale with these answers. */
    stale: null as { answers: Answer[] } | null,
    hash: () => `hash-${state.version}`,
    /** The approval view, as GET approval and PUT answers both answer it. */
    view: () => ({
      state: state.application?.state ?? "queued",
      dry_run: false,
      answers_hash: state.hash(),
      answers: state.answers,
      documents: state.documents,
      screenshots: state.screenshots,
    }),
  };
  server.use(
    http.get("*/api/applications", () =>
      HttpResponse.json({
        items: state.application ? [state.application] : [],
      }),
    ),
    http.get("*/api/applications/:id/approval", async () => {
      state.reads += 1;
      if (state.hold) await state.hold;
      return HttpResponse.json(state.view());
    }),
    http.get("*/api/applications/:id", () =>
      HttpResponse.json({ ...state.application, events: state.events }),
    ),
    http.post("*/api/postings/:id/applications", () => {
      if (state.startRefusal) {
        return HttpResponse.json(
          { ...state.startRefusal, application_id: null },
          { status: 409 },
        );
      }
      state.started += 1;
      state.application = fakeApplication("queued");
      return HttpResponse.json(
        { ...state.application, warnings: [] },
        { status: 201 },
      );
    }),
    http.put(
      "*/api/applications/:id/answers/:field",
      async ({ params, request }) => {
        const body = (await request.json()) as { value: unknown };
        const field = String(params.field);
        state.puts.push({ field, body });
        state.answers = state.answers.map((one) =>
          one.field_key === field
            ? {
                ...one,
                value: body.value,
                source: "person",
                you_answer: false,
              }
            : one,
        );
        state.version += 1;
        return HttpResponse.json(state.view());
      },
    ),
    http.post("*/api/applications/:id/approve", async ({ request }) => {
      const body = (await request.json()) as { answers_hash: string };
      state.approves.push(body.answers_hash);
      if (state.stale) {
        state.answers = state.stale.answers;
        state.version += 1;
        state.stale = null;
        return HttpResponse.json(
          {
            detail: "The form changed.",
            code: "stale",
            answers_hash: state.hash(),
            answers: state.answers,
          },
          { status: 409 },
        );
      }
      state.application = fakeApplication("approved");
      return HttpResponse.json(state.application);
    }),
    http.post("*/api/applications/:id/:action", ({ params }) => {
      const action = String(params.action);
      state.decisions.push(action);
      if (state.decisionRefusal) {
        const refusal = state.decisionRefusal;
        state.decisionRefusal = null;
        return HttpResponse.json(refusal, { status: 409 });
      }
      const next: Record<string, string> = {
        reject: "cancelled",
        cancel: "cancelled",
        submitted: "submitted",
        retry: "ready",
      };
      state.application = fakeApplication(next[action] ?? "failed");
      return HttpResponse.json(state.application);
    }),
  );
  return state;
}
