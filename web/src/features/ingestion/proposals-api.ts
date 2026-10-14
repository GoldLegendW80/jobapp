import { z } from "zod";

import { request } from "@/lib/api";

/**
 * What a run proposes for the profile (task G12): `GET /api/ingestion/runs/{id}/proposals`.
 * Values and excerpts come from the user's documents: they are data, shown as text.
 */

const documentRefSchema = z.object({
  id: z.string(),
  name: z.string().nullable(),
  deleted: z.boolean(),
});
export type DocumentRef = z.infer<typeof documentRefSchema>;

const citationSchema = z.object({
  document_id: z.string(),
  /** null once the document was deleted: the excerpt is all that is left of the source. */
  document: documentRefSchema.nullable(),
  excerpt: z.string(),
  excerpt_cut: z.boolean().default(false),
  start: z.number(),
  end: z.number(),
  transcribed: z.boolean().default(false),
});
export type Citation = z.infer<typeof citationSchema>;

export const PROPOSAL_STATUSES = [
  "pending",
  "accepted",
  "rejected",
  "superseded",
  "invalid",
  "undone",
] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];

const itemSchema = z.object({
  id: z.string(),
  status: z.enum(PROPOSAL_STATUSES),
  section: z.string(),
  key: z.string(),
  slot: z.string(),
  action: z.string(),
  value: z.unknown(),
  before: z.unknown(),
  after: z.unknown(),
  noop: z.boolean(),
  stale: z.boolean(),
  stale_reason: z.string().nullable(),
  locked: z.boolean(),
  bulk: z.string().nullable(),
  problem: z.string().nullable(),
  flags: z.array(z.string()),
  free_text: z.boolean(),
  citations: z.array(citationSchema),
  conflict: z.array(
    z.object({ value: z.unknown(), citations: z.array(citationSchema) }),
  ),
  depends_on: z.array(z.string()),
  base_revision: z.number().nullable(),
  applied_revision: z.number().nullable(),
});
export type ProposalItem = z.infer<typeof itemSchema>;

const countsSchema = z.object({
  total: z.number(),
  pending: z.number(),
  accepted: z.number(),
  rejected: z.number(),
  superseded: z.number(),
  invalid: z.number(),
  undone: z.number(),
});

const sectionSchema = z.object({
  section: z.string(),
  counts: countsSchema,
  items: z.array(itemSchema),
});
export type ProposalSection = z.infer<typeof sectionSchema>;

export const reviewSchema = z.object({
  run_id: z.string(),
  status: z.string(),
  partial: z.boolean(),
  profile_revision: z.number(),
  counts: countsSchema,
  documents: z.array(documentRefSchema),
  sections: z.array(sectionSchema),
});
export type Review = z.infer<typeof reviewSchema>;

/** Only what this list needs of `GET /api/ingestion/runs`: which run finished last. */
const runListSchema = z.object({
  runs: z.array(z.object({ id: z.string(), status: z.string() })),
});

const TIMEOUT_MS = 30_000;

export const proposalsKey = (runId: string) =>
  ["ingestion", "runs", runId, "proposals"] as const;
export const finishedRunKey = ["ingestion", "runs", "finished"] as const;

export function getProposals(
  runId: string,
  signal?: AbortSignal,
): Promise<Review> {
  return request(`/ingestion/runs/${encodeURIComponent(runId)}/proposals`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => reviewSchema.parse(body),
  });
}

/** The id of the user's latest finished run, or null when no run has finished yet. */
export async function getFinishedRunId(
  signal?: AbortSignal,
): Promise<string | null> {
  const list = await request("/ingestion/runs", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => runListSchema.parse(body),
  });
  return list.runs.find((run) => run.status === "done")?.id ?? null;
}
