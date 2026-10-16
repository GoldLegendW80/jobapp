/**
 * A fake of the run routes of the ingestion module for the vitest suites: the estimate, the
 * consent, the start, the run and its three actions. It answers on the exact paths of
 * api/app/ingestion/estimate_router.py and runs_router.py, and RECORDS every call with its
 * method, path and body, so a test asserts what the page sent and where. Like the real start it
 * refuses a `quote_id` that is not the one of the estimate of now, and like the real Continue
 * it refuses an `adds` that is not `tokens.continue_adds` of now.
 */
import { http, HttpResponse } from "msw";

import type {
  Consent,
  Estimate,
  Run,
  RunStep,
} from "@/features/ingestion/runs-api";

import { server } from "./server";

export const QUOTE_A = "a".repeat(64);
export const QUOTE_B = "b".repeat(64);
export const CONSENT_TEXT =
  "When you start a run, the app sends the text of the documents you selected to Anthropic.";
export const REMINDER =
  "The selected documents are sent to Anthropic through your own credential.";
export const OVERSHOOT = "A run can pass its ceiling by one request, or two.";

export function fakeEstimate(over: Partial<Estimate> = {}): Estimate {
  return {
    documents: [
      {
        id: "d1",
        name: "dossier.txt",
        characters: 12_345,
        images: 0,
        steps: 1,
        reused_steps: 0,
      },
      {
        id: "d2",
        name: "cv.txt",
        characters: 2_100,
        images: 0,
        steps: 1,
        reused_steps: 0,
      },
    ],
    unreadable: [],
    characters: 14_445,
    images: 0,
    steps: 2,
    reused_steps: 0,
    credential_kind: "claude_subscription",
    model: "claude-opus-5-5",
    model_reason:
      "Your credential is a Claude subscription, so the run uses claude-opus-5-5.",
    tokens: { low: 61_234, high: 176_220, ceiling: 352_440 },
    cost: null,
    cost_note:
      "On a Claude subscription, tokens count against your quota, not as money.",
    limits: {
      documents: 30,
      characters: 1_500_000,
      images: 60,
      steps: 60,
      step_minutes: 15,
      run_minutes: 60,
    },
    consent: { version: 1, accepted: false },
    can_start: true,
    blockers: [],
    quote_id: QUOTE_A,
    ...over,
  };
}

/** The same selection for a user whose credential is an API key: amounts, another model. */
export function fakeKeyEstimate(over: Partial<Estimate> = {}): Estimate {
  return fakeEstimate({
    credential_kind: "anthropic_api_key",
    model: "claude-sonnet-5-5",
    model_reason:
      "Your credential is an Anthropic API key, so the run uses claude-sonnet-5-5.",
    cost: {
      currency: "USD",
      low: "0.21",
      high: "1.70",
      ceiling: "3.40",
      prices_as_of: "2026-10-01",
      note: "Estimates from list prices read on 2026-10-01, rounded to the cent. Your provider's invoice is the exact figure.",
    },
    cost_note: null,
    quote_id: QUOTE_B,
    ...over,
  });
}

export function fakeStep(over: Partial<RunStep> = {}): RunStep {
  return {
    id: `s${over.position ?? 0}`,
    position: 0,
    kind: "extract",
    document_id: "d1",
    document_name: "dossier.txt",
    part: 0,
    status: "planned",
    attempt: 1,
    items: 0,
    refused: 0,
    two_digit_year: 0,
    agent_run_id: null,
    tokens: null,
    cost_usd: null,
    error: null,
    ...over,
  };
}

type RunOver = Partial<Omit<Run, "tokens" | "counts">> & {
  tokens?: Partial<Run["tokens"]>;
  counts?: Partial<Run["counts"]>;
};

export function fakeRun(over: RunOver = {}): Run {
  const { tokens, counts, ...rest } = over;
  const ceiling = tokens?.ceiling ?? 352_440;
  const adds = tokens?.continue_adds ?? null;
  return {
    id: "r1",
    status: "running",
    partial: false,
    paused_reason: null,
    closed_reason: null,
    waiting: null,
    cost_usd: null,
    cancel_requested: false,
    document_ids: ["d1", "d2"],
    unread_document_ids: [],
    current_agent_run_id: null,
    tokens: {
      spent: 3_760,
      ceiling,
      low: 61_234,
      high: 176_220,
      history: [],
      continue_adds: null,
      // As the server builds it, unless the test says otherwise.
      continue_ceiling: adds !== null && adds > 0 ? ceiling + adds : null,
      overshoot_requests: 2,
      overshoot: OVERSHOOT,
      ...tokens,
    },
    counts: {
      steps: 2,
      planned: 0,
      running: 1,
      done: 1,
      reused: 0,
      failed: 0,
      cancelled: 0,
      items: 3,
      refused: 0,
      two_digit_year: 0,
      proposals: 0,
      ...counts,
    },
    steps: [
      fakeStep({ position: 0, status: "done", items: 3, tokens: 3_760 }),
      fakeStep({
        position: 1,
        status: "running",
        document_id: "d2",
        document_name: "cv.txt",
      }),
    ],
    created_at: "2026-10-13T09:00:00Z",
    deadline_at: "2026-10-13T10:00:00Z",
    finished_at: null,
    ...rest,
  };
}

export interface RunCall {
  method: string;
  /** The path as the page asked for it, with its `/api` in front. */
  path: string;
  body: unknown;
}

export interface FakeRuns {
  calls: RunCall[];
  /** What the estimate route answers now. A start is compared with its `quote_id`. */
  estimate: Estimate;
  consent: Consent;
  /** Newest first. */
  runs: Run[];
  /** The run a start that is accepted creates. */
  created: Run;
  /** What a Resume or a Continue that is accepted turns the run into. */
  afterAction: (run: Run, action: string) => Run;
  /** Answers given before the default ones, first in first out, for one start each. */
  startAnswers: Array<() => Response>;
  /** The calls of one method and path. */
  sent: (method: string, path: string) => RunCall[];
}

const isActive = (run: Run) =>
  run.status === "running" || run.status === "paused";

export function fakeRuns(initial: Partial<FakeRuns> = {}): FakeRuns {
  const state: FakeRuns = {
    calls: [],
    estimate: fakeEstimate(),
    consent: {
      version: 1,
      text: CONSENT_TEXT,
      reminder: REMINDER,
      accepted: false,
      accepted_at: null,
    },
    runs: [],
    created: fakeRun(),
    afterAction: (run) => ({
      ...run,
      status: "running",
      paused_reason: null,
      tokens: { ...run.tokens, continue_adds: null, continue_ceiling: null },
    }),
    startAnswers: [],
    sent: (method, path) =>
      state.calls.filter(
        (call) => call.method === method && call.path === path,
      ),
    ...initial,
  };
  const record = async (request: Request): Promise<unknown> => {
    const text = await request.text();
    const body: unknown = text === "" ? undefined : JSON.parse(text);
    state.calls.push({
      method: request.method,
      path: new URL(request.url).pathname,
      body,
    });
    return body;
  };
  const find = (id: unknown) => state.runs.find((run) => run.id === id);
  const missing = () =>
    HttpResponse.json({ detail: "Run not found." }, { status: 404 });

  server.use(
    http.post("/api/ingestion/runs/estimate", async ({ request }) => {
      await record(request);
      return HttpResponse.json({
        ...state.estimate,
        consent: {
          version: state.consent.version,
          accepted: state.consent.accepted,
        },
      });
    }),
    http.get("/api/ingestion/consent", async ({ request }) => {
      await record(request);
      return HttpResponse.json(state.consent);
    }),
    http.post("/api/ingestion/consent", async ({ request }) => {
      const body = (await record(request)) as { version?: unknown };
      if (body.version !== state.consent.version)
        return HttpResponse.json(
          {
            detail:
              "The consent text has changed. Read the current text and accept it.",
          },
          { status: 422 },
        );
      state.consent = {
        ...state.consent,
        accepted: true,
        accepted_at: "2026-10-13T09:00:00Z",
      };
      return HttpResponse.json(state.consent);
    }),
    http.get("/api/ingestion/runs", async ({ request }) => {
      await record(request);
      return HttpResponse.json({
        runs: state.runs.map((run) => ({
          id: run.id,
          status: run.status,
          partial: run.partial,
          created_at: run.created_at,
          finished_at: run.finished_at,
        })),
        active_run_id: state.runs.find(isActive)?.id ?? null,
      });
    }),
    http.post("/api/ingestion/runs", async ({ request }) => {
      const body = (await record(request)) as { quote_id?: unknown };
      const scripted = state.startAnswers.shift();
      if (scripted) return scripted();
      if (!state.consent.accepted)
        return HttpResponse.json(
          {
            detail: "Read the consent text and accept it first.",
            code: "consent_required",
            consent_version: state.consent.version,
          },
          { status: 409 },
        );
      if (body.quote_id !== state.estimate.quote_id)
        return HttpResponse.json(
          {
            detail: "The estimate has changed.",
            code: "estimate_changed",
            estimate: {
              ...state.estimate,
              consent: { version: state.consent.version, accepted: true },
            },
          },
          { status: 409 },
        );
      state.runs.unshift(state.created);
      return HttpResponse.json(state.created, { status: 201 });
    }),
    http.get("/api/ingestion/runs/:id", async ({ request, params }) => {
      await record(request);
      const run = find(params.id);
      return run ? HttpResponse.json(run) : missing();
    }),
    http.post(
      "/api/ingestion/runs/:id/:action",
      async ({ request, params }) => {
        const body = (await record(request)) as { adds?: unknown } | undefined;
        const run = find(params.id);
        if (!run) return missing();
        const replace = (updated: Run) => {
          state.runs = state.runs.map((one) =>
            one.id === run.id ? updated : one,
          );
          return HttpResponse.json(updated);
        };
        const action = String(params.action);
        if (action === "cancel")
          return replace({
            ...run,
            status: "cancelled",
            paused_reason: null,
            finished_at: "2026-10-13T09:30:00Z",
            tokens: {
              ...run.tokens,
              continue_adds: null,
              continue_ceiling: null,
            },
          });
        if (action === "continue") {
          if (body?.adds !== run.tokens.continue_adds)
            return HttpResponse.json(
              {
                detail: "What a Continue adds has changed.",
                code: "continue_changed",
                continue_adds: run.tokens.continue_adds,
                run,
              },
              { status: 409 },
            );
          return replace(state.afterAction(run, action));
        }
        if (action === "resume") return replace(state.afterAction(run, action));
        return missing();
      },
    ),
  );
  return state;
}
