import type {
  AssessedRequirement,
  AssessmentResult,
} from "@/features/jobs/api/assessment-api";

/** The words of an assessment's codes, as the posting page shows them. */
export const IMPORTANCE_WORDS: Record<
  AssessedRequirement["importance"],
  string
> = {
  critical: "Critical",
  high: "High",
  meaningful: "Meaningful",
  preferred: "Preferred",
  low_signal: "Low signal",
};

export const TIER_WORDS: Record<AssessedRequirement["tier"], string> = {
  stated: "stated in the posting",
  structural: "from the posting's structure",
  inferred: "inferred",
};

export const MATCH_WORDS: Record<AssessedRequirement["match"], string> = {
  strong: "Strong",
  partial: "Partial",
  missing: "Missing",
  not_applicable: "Met",
};

export const FINDING_WORDS: Record<
  AssessmentResult["culture"][number]["finding"],
  string
> = {
  fits: "Fits",
  conflicts: "Conflicts",
  not_stated: "Not said",
};

export const SIGNAL_WORDS: Record<
  AssessmentResult["signals"][number]["kind"],
  string
> = {
  specificity: "How specific it is",
  contradiction: "A contradiction",
  contractor_language: "Contractor wording",
  buzzword_mismatch: "AI wording beyond the role",
  fixed_term: "Fixed term",
};

export const CONFIDENCE_WORDS: Record<AssessmentResult["confidence"], string> =
  {
    high: "High",
    medium: "Medium",
    low: "Low",
  };

export const CLARITY_WORDS: Record<
  AssessmentResult["role_clarity"]["points"][number]["status"],
  string
> = {
  present: "Said",
  partial: "Partly said",
  missing: "Not said",
};

/** The critical or high requirements the person meets partly or not at all. */
export function gapCount(result: AssessmentResult): number {
  return result.requirements.filter(
    (row) =>
      (row.importance === "critical" || row.importance === "high") &&
      (row.match === "missing" || row.match === "partial"),
  ).length;
}
