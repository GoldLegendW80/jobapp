import type { JobDetail } from "@/features/jobs/api/jobs-api";
import type { Liveness } from "@/features/jobs/api/liveness-api";
import { makeDetail } from "@/test/jobs";

/** What the checks of invented postings found, as the api stores them (SPEC-jobs, "Liveness"). */
export const CHECKED_AT = "2026-11-07T09:30:00Z";

export const MAY_BE_CLOSED_CHECK: Liveness = {
  verdict: "may_be_closed",
  reason: "page_hints",
  sentence: "Job expired",
  checked_at: CHECKED_AT,
};

export const CLOSED_CHECK: Liveness = {
  verdict: "closed",
  reason: "page_says",
  sentence: "Cette offre n’est plus disponible.",
  checked_at: CHECKED_AT,
};

export const OPEN_CHECK: Liveness = {
  verdict: "no_closure",
  reason: "none",
  sentence: null,
  checked_at: CHECKED_AT,
};

export const SIGN_IN_CHECK: Liveness = {
  verdict: "unconfirmed",
  reason: "sign_in",
  sentence: null,
  checked_at: CHECKED_AT,
};

/** An invented posting at Lumen Freight, as its check left it. */
export function checkedPosting(
  liveness: Liveness | null,
  overrides: Partial<JobDetail> = {},
): JobDetail {
  return makeDetail({
    title: "Senior data engineer",
    company: "Lumen Freight",
    location: "Lyon, France",
    url: "https://careers.example.org/lumen-freight/4012345",
    status: "found",
    liveness,
    ...overrides,
  });
}
