/** What writing costs: the estimate of one posting's documents, as the api answers it. */
import { z } from "zod";

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
