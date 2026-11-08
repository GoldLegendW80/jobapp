/**
 * The reasons a person may give a Pass, in one tap (api passes.REASONS, from career-ops'
 * discard reasons, NOTICE). The person's own words for the Pass, never a verdict of the app.
 */
export const PASS_REASONS = {
  pay: "Pay too low",
  remote: "Not remote enough",
  stack: "Not my stack",
  seniority: "Wrong seniority",
  location: "Wrong location",
  company_size: "Company size",
  culture: "Company culture",
} as const;

export type PassReason = keyof typeof PASS_REASONS;

/** The words of a reason the api counted; an unknown one as it is. */
export const passReasonLabel = (reason: string): string =>
  (PASS_REASONS as Record<string, string>)[reason] ?? reason;
