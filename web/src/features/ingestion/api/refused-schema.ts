import { z } from "zod";

/**
 * Proposals of one section that the checks refused for one rule and that were never stored
 * afterwards (api/app/ingestion/refusals.py), as the run and its review answer them. `sentence`
 * is the one the assistant was given; the page writes its own (helpers/refusal-words.ts).
 */
export const refusedSchema = z.object({
  section: z.string(),
  rule: z.string(),
  count: z.number(),
  sentence: z.string(),
});
export type Refused = z.infer<typeof refusedSchema>;

/** What a run and its review answer: empty from a server that does not keep them yet. */
export const refusedListSchema = z.array(refusedSchema).default([]);
