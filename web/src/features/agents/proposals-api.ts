/**
 * Calls of /api/proposals (SPEC-agents.md, API): what agents suggest, and the user's decision.
 * Nothing an agent proposes changes the job until it is accepted here; accepting applies it
 * through the jobs service on the server (a job event, the job's new version).
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

export const proposalStatuses = [
  "pending",
  "accepted",
  "rejected",
  "superseded",
  "invalid",
  "undone",
] as const;
export type ProposalStatus = (typeof proposalStatuses)[number];

export const proposalSchema = z.object({
  id: z.string().min(1),
  run_id: z.string().nullable(),
  target: z.string(),
  target_id: z.string().nullable(),
  kind: z.string(),
  payload: z.record(z.string(), z.unknown()),
  status: z.enum(proposalStatuses),
  decided_by: z.enum(["user", "system"]).nullable(),
  decided_at: z.string().nullable(),
  edited_value: z.unknown().nullable().optional(),
  reason: z.string().nullable(),
  created_at: z.string(),
});
export type Proposal = z.infer<typeof proposalSchema>;

const proposalListSchema = z.object({ items: z.array(proposalSchema) });

export const proposalsKey = ["proposals"] as const;
export const jobProposalsKey = (jobId: string) =>
  [...proposalsKey, "job", jobId] as const;

export async function listJobProposals(
  jobId: string,
  signal?: AbortSignal,
): Promise<Proposal[]> {
  const query = new URLSearchParams({
    target: "job",
    target_id: jobId,
    limit: "30",
  });
  const body = await request(`/proposals?${query.toString()}`, {
    signal,
    validate: (data) => proposalListSchema.parse(data),
  });
  return body.items;
}

export function acceptProposal(
  id: string,
  body: { edited_value?: unknown; updated_at?: string },
): Promise<Proposal> {
  return request(`/proposals/${encodeURIComponent(id)}/accept`, {
    method: "POST",
    body,
    validate: (data) => proposalSchema.parse(data),
  });
}

export function rejectProposal(id: string): Promise<Proposal> {
  return request(`/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    validate: (data) => proposalSchema.parse(data),
  });
}

/** Why the api refused a decision with 409: decided already, job gone, or job changed. */
export function conflictReason(
  error: unknown,
): "not_pending" | "target_gone" | "status_changed" | "stale" | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const reason = (error.body as { reason?: unknown } | null)?.reason;
  return reason === "not_pending" ||
    reason === "target_gone" ||
    reason === "status_changed" ||
    reason === "stale"
    ? reason
    : undefined;
}
