/** What writing costs: the estimates, the consent to send a profile to the model, the spend of the
 * day and of 30 days, and what a draft of the skills section would start. */
import { z } from "zod";

import { request } from "@/lib/api";

import {
  DOCUMENTS_ROOT,
  DOCUMENTS_TIMEOUT_MS,
  getJson,
  sendJson,
} from "./documents-request";

/** A decimal arrives as a string. */
const decimal = z.union([z.string(), z.number()]);

export const estimateSchema = z.object({
  tokens: z.object({ low: z.number(), high: z.number() }),
  /** An API key only. */
  cost: z
    .object({ currency: z.string(), low: decimal, high: decimal })
    .nullable(),
});
export type Estimate = z.infer<typeof estimateSchema>;

export const consentSchema = z.object({
  version: z.number(),
  text: z.string(),
  accepted: z.boolean(),
  accepted_at: z.string().nullable(),
  /** Null without a credential. */
  model: z.string().nullable(),
  min_fit: z.number(),
  posting: estimateSchema,
  week: estimateSchema.extend({
    postings: z.number(),
    postings_30_days: z.number(),
  }),
  cost_note: z.string(),
});
export type Consent = z.infer<typeof consentSchema>;

const tokensSpent = z.object({
  input: z.number(),
  output: z.number(),
  cost_usd: decimal,
});

export const spendSchema = z.object({
  day: z.string(),
  auto: z.boolean(),
  min_fit: z.number(),
  daily_ceiling: z.number(),
  written_today: z.number(),
  waiting: z.number(),
  postings_30_days: z.number(),
  to_write: z.number(),
  to_write_estimate: estimateSchema,
  /** Of `to_write`, those under the person's own threshold: what lowering it would add. Optional
   * because answers recorded before it (other tasks' test doubles) lack it; then nothing is shown. */
  newly: z.number().optional(),
  newly_estimate: estimateSchema.optional(),
  priced: z.boolean(),
  cost_note: z.string(),
  today: tokensSpent,
  last_30_days: tokensSpent,
});
export type Spend = z.infer<typeof spendSchema>;

/** What saving a draft of the skills section would start (`POST /api/documents/spend/skills`). */
export const skillsSpendSchema = z.object({
  auto: z.boolean(),
  min_fit: z.number(),
  /** Automatic writing starts by itself: on, with the consent and a credential. */
  writes: z.boolean(),
  /** Postings the draft brings to the threshold that have no CV yet. */
  newly: z.number(),
  newly_estimate: estimateSchema,
  priced: z.boolean(),
  cost_note: z.string(),
});
export type SkillsSpend = z.infer<typeof skillsSpendSchema>;

export const consentKey = ["documents", "consent"] as const;
export const spendKey = (minFit?: number) =>
  ["documents", "spend", minFit ?? "current"] as const;

export const getConsent = (signal?: AbortSignal): Promise<Consent> =>
  getJson(`${DOCUMENTS_ROOT}/consent`, consentSchema, signal);

/** Records the consent to the text of `version`, the one the dialog showed. */
export const acceptConsent = (version: number): Promise<Consent> =>
  sendJson(`${DOCUMENTS_ROOT}/consent`, "POST", consentSchema, { version });

export const getSkillsSpend = (
  skills: unknown,
  signal?: AbortSignal,
): Promise<SkillsSpend> =>
  request(`${DOCUMENTS_ROOT}/spend/skills`, {
    method: "POST",
    body: { skills },
    signal,
    timeoutMs: DOCUMENTS_TIMEOUT_MS,
    validate: (answer) => skillsSpendSchema.parse(answer),
  });

export const getSpend = (
  minFit?: number,
  signal?: AbortSignal,
): Promise<Spend> =>
  getJson(
    `${DOCUMENTS_ROOT}/spend${minFit === undefined ? "" : `?min_fit=${String(minFit)}`}`,
    spendSchema,
    signal,
  );
