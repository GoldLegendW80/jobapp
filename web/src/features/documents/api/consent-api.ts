/** What writing costs: the estimate of one posting's documents and the consent to send a profile
 * to the model. */
import { z } from "zod";

import { DOCUMENTS_ROOT, getJson, sendJson } from "./documents-request";

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
  /** One posting's CV and letter, their hiring panels included (documents D29). */
  posting: estimateSchema,
  cost_note: z.string(),
});
export type Consent = z.infer<typeof consentSchema>;

export const consentKey = ["documents", "consent"] as const;

export const getConsent = (signal?: AbortSignal): Promise<Consent> =>
  getJson(`${DOCUMENTS_ROOT}/consent`, consentSchema, signal);

/** Records the consent to the text of `version`, the one the dialog showed. */
export const acceptConsent = (version: number): Promise<Consent> =>
  sendJson(`${DOCUMENTS_ROOT}/consent`, "POST", consentSchema, { version });
