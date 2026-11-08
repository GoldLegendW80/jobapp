/**
 * What the last check of a posting's own address found (SPEC-jobs, "Liveness";
 * api/app/jobs/liveness_check.py), on a posting and on a Discover card. The verdict and the
 * reason are read as plain strings: one a newer api adds shows nothing instead of failing.
 */
import { z } from "zod";

export const livenessSchema = z.object({
  verdict: z.string(),
  reason: z.string(),
  sentence: z.string().nullable().optional(),
  checked_at: z.string(),
  /** This check moved the posting to Closed; absent on a check stored before the field. */
  moved: z.boolean().optional(),
});
export type Liveness = z.infer<typeof livenessSchema>;
