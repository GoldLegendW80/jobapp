/**
 * What the person should know about a posting before the click (api/app/jobs/notices.py,
 * SPEC-jobs "Before the click"): the posting page, the Discover card and the Apply confirmation
 * carry the same list. Kind, code and level are read as plain strings, so a value a newer api
 * adds still renders.
 */
import { z } from "zod";

export const noticeSchema = z.object({
  /** signal, closing, cross_listing or history */
  kind: z.string(),
  code: z.string(),
  /** The sentence the person reads: facts, never a verdict. */
  text: z.string(),
  /** The posting's own words, when the notice comes from its text. */
  quote: z.string().nullable().optional(),
  /** warning or info */
  level: z.string(),
  /** The other posting it names: the one applied to, or the one with the same text. */
  job_id: z.string().nullable().optional(),
});
export type Notice = z.infer<typeof noticeSchema>;

export const noticesSchema = z.array(noticeSchema);
