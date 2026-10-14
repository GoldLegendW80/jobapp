/**
 * Calls of the run routes (api/app/ingestion/estimate_router.py and runs_router.py): the
 * estimate with its `quote_id`, the consent text, the start, the run of the page and its three
 * actions. Shapes mirror the routers' response models. Every figure the page shows (tokens,
 * ceiling, amounts, what a Continue adds) is read from these answers and never computed here.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { z } from "zod";

import { errorDetail, isApiError, request } from "@/lib/api";

const TIMEOUT_MS = 30_000;
const RUNS = "/ingestion/runs";
const CONSENT = "/ingestion/consent";

// --- the estimate -------------------------------------------------------------------------------

export const estimateSchema = z.object({
  /** The documents the run would read, in the order selected. */
  documents: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      characters: z.number(),
      images: z.number(),
      steps: z.number(),
      reused_steps: z.number(),
    }),
  ),
  /** The selected documents the run would leave out, each with the server's sentence. */
  unreadable: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      code: z.string(),
      reason: z.string(),
    }),
  ),
  characters: z.number(),
  images: z.number(),
  steps: z.number(),
  reused_steps: z.number(),
  credential_kind: z.string().nullable(),
  model: z.string().nullable(),
  model_reason: z.string(),
  tokens: z.object({
    low: z.number(),
    high: z.number(),
    ceiling: z.number(),
  }),
  /** Null unless the credential is an API key. Amounts are text with two decimals. */
  cost: z
    .object({
      currency: z.string(),
      low: z.string(),
      high: z.string(),
      ceiling: z.string(),
      prices_as_of: z.string(),
      note: z.string(),
    })
    .nullable(),
  /** The sentence about quota, on a subscription. */
  cost_note: z.string().nullable(),
  limits: z.object({
    documents: z.number(),
    characters: z.number(),
    images: z.number(),
    steps: z.number(),
    step_minutes: z.number(),
    run_minutes: z.number(),
  }),
  consent: z.object({ version: z.number(), accepted: z.boolean() }),
  can_start: z.boolean(),
  blockers: z.array(z.object({ code: z.string(), reason: z.string() })),
  /** Sent back unchanged with the start: it names the figures the user was shown. */
  quote_id: z.string(),
});
export type Estimate = z.infer<typeof estimateSchema>;

export interface Selection {
  document_ids: string[];
  reread_document_ids: string[];
}

export function getEstimate(
  selection: Selection,
  signal?: AbortSignal,
): Promise<Estimate> {
  return request(`${RUNS}/estimate`, {
    method: "POST",
    body: selection,
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => estimateSchema.parse(body),
  });
}

// --- the consent --------------------------------------------------------------------------------

export const consentSchema = z.object({
  version: z.number(),
  text: z.string(),
  reminder: z.string(),
  accepted: z.boolean(),
  accepted_at: z.string().nullable(),
});
export type Consent = z.infer<typeof consentSchema>;

export const consentKey = ["ingestion", "consent"] as const;

export function getConsent(signal?: AbortSignal): Promise<Consent> {
  return request(CONSENT, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => consentSchema.parse(body),
  });
}

/** Records the consent to the text of `version`, the one the dialog showed. */
export function acceptConsent(version: number): Promise<Consent> {
  return request(CONSENT, {
    method: "POST",
    body: { version },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => consentSchema.parse(body),
  });
}

// --- a run --------------------------------------------------------------------------------------

export const RUN_STATUSES = [
  "running",
  "paused",
  "done",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const stepSchema = z.object({
  id: z.string(),
  position: z.number(),
  kind: z.string(),
  document_id: z.string(),
  /** Null once the document is deleted. */
  document_name: z.string().nullable(),
  part: z.number().nullable(),
  status: z.string(),
  attempt: z.number(),
  items: z.number(),
  refused: z.number(),
  two_digit_year: z.number(),
  agent_run_id: z.string().nullable(),
  /** The step's last attempt only: the steps do not add up to `tokens.spent`. */
  tokens: z.number().nullable(),
  cost_usd: z.string().nullable(),
  error: z.string().nullable(),
});
export type RunStep = z.infer<typeof stepSchema>;

export const runSchema = z.object({
  id: z.string(),
  status: z.enum(RUN_STATUSES),
  partial: z.boolean(),
  /** `token_ceiling`, or the sentence of the failure that paused the run. */
  paused_reason: z.string().nullable(),
  /** The sentence of a run the engine closed because it could not move it. */
  closed_reason: z.string().nullable().default(null),
  waiting: z.string().nullable(),
  cancel_requested: z.boolean(),
  document_ids: z.array(z.string()),
  unread_document_ids: z.array(z.string()),
  current_agent_run_id: z.string().nullable(),
  tokens: z.object({
    spent: z.number(),
    ceiling: z.number(),
    low: z.number(),
    high: z.number(),
    history: z.array(
      z.object({
        at: z.string(),
        spent: z.number(),
        from: z.number(),
        to: z.number(),
      }),
    ),
    /** What one Continue adds now, for a run paused on its ceiling. Zero: nothing left to run. */
    continue_adds: z.number().nullable(),
    overshoot_requests: z.number(),
    overshoot: z.string(),
  }),
  counts: z.object({
    steps: z.number(),
    planned: z.number(),
    running: z.number(),
    done: z.number(),
    reused: z.number(),
    failed: z.number(),
    cancelled: z.number(),
    items: z.number(),
    refused: z.number(),
    two_digit_year: z.number(),
    proposals: z.number(),
  }),
  steps: z.array(stepSchema),
  created_at: z.string(),
  deadline_at: z.string(),
  finished_at: z.string().nullable(),
});
export type Run = z.infer<typeof runSchema>;

export const runListSchema = z.object({
  runs: z.array(
    z.object({
      id: z.string(),
      status: z.enum(RUN_STATUSES),
      partial: z.boolean(),
      created_at: z.string(),
      finished_at: z.string().nullable(),
    }),
  ),
  active_run_id: z.string().nullable(),
});
export type RunList = z.infer<typeof runListSchema>;

/** The list of the user's runs: the page finds its run here after a reload or in another tab. */
export const runsKey = ["ingestion", "runs"] as const;
export const runKey = (id: string) => ["ingestion", "runs", id] as const;

export const PAUSED_ON_CEILING = "token_ceiling";

export const isActive = (status: RunStatus) =>
  status === "running" || status === "paused";

export function listRuns(signal?: AbortSignal): Promise<RunList> {
  return request(RUNS, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => runListSchema.parse(body),
  });
}

export function getRun(id: string, signal?: AbortSignal): Promise<Run> {
  return request(`${RUNS}/${encodeURIComponent(id)}`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => runSchema.parse(body),
  });
}

/**
 * Starts a run over the selection the estimate was asked for, echoing the `quote_id` of the
 * estimate the dialog shows. The server compares it with the estimate of now and creates no run
 * when they differ (`startRefusal` reads that answer).
 */
export function startRun(selection: Selection, quoteId: string): Promise<Run> {
  return request(RUNS, {
    method: "POST",
    body: { ...selection, quote_id: quoteId },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => runSchema.parse(body),
  });
}

function act(id: string, action: string, body?: unknown): Promise<Run> {
  return request(`${RUNS}/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    body,
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => runSchema.parse(answer),
  });
}

/** Leaves a pause that is not the one on the ceiling. */
export const resumeRun = (id: string) => act(id, "resume");
export const cancelRun = (id: string) => act(id, "cancel");
/**
 * Raises the ceiling by `adds`, the figure the page showed (`tokens.continue_adds`). The server
 * applies that figure or nothing (`continueRefusal` reads the answer of a figure that changed).
 */
export const continueRun = (id: string, adds: number) =>
  act(id, "continue", { adds });

// --- the run of the page --------------------------------------------------------------------

/** How often the page looks for a run started elsewhere (another tab) while none moves. */
export const IDLE_POLL_MS = 15_000;
/** How often the run on the page is read again while it can still move. */
export const RUN_POLL_MS = 2000;

/** A closed run may keep one step at work for a few seconds: its tokens are still to come. */
const canMove = (run: Run | undefined) =>
  run === undefined ||
  isActive(run.status) ||
  run.steps.some((step) => step.status === "running");

/**
 * The run this page shows, read from the server and read again while it moves: the active one,
 * else the latest. Nothing about it is kept in memory only, so a reload or a second tab shows
 * the same run in the same state. The review list (proposal-list.tsx) calls this too.
 */
export function useShownRun() {
  const client = useQueryClient();
  const runs = useQuery({
    queryKey: runsKey,
    queryFn: ({ signal }) => listRuns(signal),
    refetchInterval: IDLE_POLL_MS,
  });
  const id = runs.data?.active_run_id ?? runs.data?.runs[0]?.id;
  const run = useQuery({
    queryKey: runKey(id ?? "none"),
    queryFn: ({ signal }) => getRun(id ?? "", signal),
    enabled: id !== undefined,
    refetchInterval: (query) =>
      canMove(query.state.data) ? RUN_POLL_MS : false,
  });
  // The list says which run is active: ask it again whenever this run changes state.
  const status = run.data?.status;
  const seen = useRef<{ id: string; status: string }>();
  useEffect(() => {
    if (id === undefined || status === undefined) return;
    const before = seen.current;
    seen.current = { id, status };
    if (before !== undefined && before.id === id && before.status !== status)
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
  }, [client, id, status]);
  return { id, run, listed: runs };
}

// --- refusals -----------------------------------------------------------------------------------

export type StartRefusal =
  /** The figures changed since the dialog showed them: these are the ones of now. */
  | { kind: "estimate_changed"; sentence: string; estimate: Estimate }
  /** No consent to the current text is recorded (a new version, or another tab). */
  | { kind: "consent_required"; sentence: string }
  /** A run is already active: the page opens it. */
  | { kind: "active_run"; sentence: string; runId: string | undefined }
  /** Some selected documents cannot be read: the reason of each. */
  | {
      kind: "unreadable_documents";
      sentence: string;
      documents: { id: string; reason: string }[];
    }
  | { kind: "other"; sentence: string };

const START_FAILED = "The run could not be started. Try again in a moment.";

const refusedDocuments = z.array(
  z.object({ id: z.string(), reason: z.string() }),
);

/** What a start that did not answer 201 means for the dialog. Never a success. */
export function startRefusal(error: unknown): StartRefusal {
  if (!isApiError(error) || error.kind !== "http")
    return { kind: "other", sentence: actionFailure(error, START_FAILED) };
  const body = (error.body ?? {}) as Record<string, unknown>;
  const said = errorDetail(error);
  if (error.status === 409 && body.code === "estimate_changed") {
    const estimate = estimateSchema.safeParse(body.estimate);
    if (estimate.success)
      return {
        kind: "estimate_changed",
        sentence:
          said ?? "The estimate has changed. Read the new figures first.",
        estimate: estimate.data,
      };
  }
  if (error.status === 409 && body.code === "consent_required")
    return {
      kind: "consent_required",
      sentence: said ?? "Read the text below and accept it first.",
    };
  if (error.status === 409 && body.code === "active_run")
    return {
      kind: "active_run",
      sentence: said ?? "A run is already in progress.",
      runId: typeof body.run_id === "string" ? body.run_id : undefined,
    };
  if (error.status === 422 && body.code === "unreadable_documents") {
    const documents = refusedDocuments.safeParse(body.documents);
    if (documents.success)
      return {
        kind: "unreadable_documents",
        sentence: said ?? "Some of the selected documents cannot be read.",
        documents: documents.data,
      };
  }
  const spoken =
    error.status === 409 || error.status === 422 ? said : undefined;
  return {
    kind: "other",
    sentence: spoken ?? actionFailure(error, START_FAILED),
  };
}

export interface ContinueRefusal {
  /** What one Continue adds now: shown, and agreed to by another click. */
  continueAdds: number;
  run: Run | undefined;
}

/** The answer of a Continue whose figure is no longer the one of now: nothing was added. */
export function continueRefusal(error: unknown): ContinueRefusal | undefined {
  if (!isApiError(error) || error.kind !== "http" || error.status !== 409)
    return undefined;
  const body = (error.body ?? {}) as Record<string, unknown>;
  if (body.code !== "continue_changed") return undefined;
  if (typeof body.continue_adds !== "number") return undefined;
  const run = runSchema.safeParse(body.run);
  return {
    continueAdds: body.continue_adds,
    run: run.success ? run.data : undefined,
  };
}

/** One sentence for an action that failed: the server's on a 409, else ours. */
export function actionFailure(error: unknown, fallback: string): string {
  if (!isApiError(error)) return fallback;
  if (error.kind === "http") {
    if (error.status === 401) return "Your session has ended. Sign in again.";
    if (error.status === 409) return errorDetail(error) ?? fallback;
    return fallback;
  }
  if (error.kind === "timeout")
    return "The server took too long to answer. Try again.";
  if (error.kind === "parse")
    return "The server sent an answer this page cannot read. Reload the page.";
  return "It did not go through. Check your connection and try again.";
}

/** "1,234,567": the figure as the server gave it, grouped by thousands for reading. */
export const tokens = (value: number) => value.toLocaleString("en-US");
