/**
 * Assess this posting (SPEC-jobs.md, "Assess this posting"): GET /api/jobs/{id}/assessment, the
 * posting's last assessment and its latest run, and POST /api/jobs/{id}/assess, the click that
 * starts the agent (a second click while one runs answers that run).
 */
import { z } from "zod";

import { request } from "@/lib/api";
import { runSchema, runStatuses } from "@/features/agents/api/runs-api";

import { jobsKey } from "./jobs-api";

export const importances = [
  "critical",
  "high",
  "meaningful",
  "preferred",
  "low_signal",
] as const;
export const matches = [
  "strong",
  "partial",
  "missing",
  "not_applicable",
] as const;
export const signalKinds = [
  "specificity",
  "contradiction",
  "contractor_language",
  "buzzword_mismatch",
  "fixed_term",
] as const;

const requirementSchema = z.object({
  requirement: z.string(),
  importance: z.enum(importances),
  tier: z.enum(["stated", "structural", "inferred"]),
  signal: z.string().nullable(),
  match: z.enum(matches),
  profile_evidence: z.string(),
  interview_risk: z.string().nullable(),
  mitigation: z.string().nullable(),
});
export type AssessedRequirement = z.infer<typeof requirementSchema>;

const clarityPointSchema = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["present", "partial", "missing"]),
  evidence: z.array(z.string()),
});

const resultSchema = z.object({
  requirements: z.array(requirementSchema),
  more_requirements: z.number().int().nonnegative(),
  culture: z.array(
    z.object({
      criterion: z.string(),
      finding: z.enum(["fits", "conflicts", "not_stated"]),
      evidence: z.string(),
    }),
  ),
  signals: z.array(
    z.object({
      kind: z.enum(signalKinds),
      quote: z.string(),
      note: z.string(),
    }),
  ),
  verdict: z.string(),
  confidence: z.enum(["high", "medium", "low"]),
  role_clarity: z.object({
    read: z.boolean(),
    points: z.array(clarityPointSchema),
    questions: z.array(z.string()),
  }),
});
export type AssessmentResult = z.infer<typeof resultSchema>;

const viewSchema = z.object({
  run: z
    .object({
      id: z.string(),
      status: z.enum(runStatuses),
      error: z.string().nullable(),
    })
    .nullable(),
  assessment: z
    .object({
      run_id: z.string().nullable(),
      created_at: z.string(),
      result: resultSchema,
    })
    .nullable(),
});
export type AssessmentView = z.infer<typeof viewSchema>;

export const assessmentKey = (jobId: string) =>
  [...jobsKey, "assessment", jobId] as const;

export function getAssessment(
  jobId: string,
  signal?: AbortSignal,
): Promise<AssessmentView> {
  return request(`/jobs/${encodeURIComponent(jobId)}/assessment`, {
    signal,
    validate: (data) => viewSchema.parse(data),
  });
}

export function startAssessment(jobId: string) {
  return request(`/jobs/${encodeURIComponent(jobId)}/assess`, {
    method: "POST",
    validate: (data) => runSchema.parse(data),
  });
}
