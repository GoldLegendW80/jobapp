/**
 * The vocabulary of the jobs module as the user reads it: statuses in pipeline order with their
 * labels, contract types, sort orders, and the colour tone of a fit score. A value the api adds
 * later and this build does not know still gets a readable label (see `statusLabel`).
 */

export const STATUSES = [
  "found",
  "shortlisted",
  "cv_ready",
  "staged",
  "applied",
  "interview",
  "rejected",
  "closed",
  "skipped",
] as const;
export type JobStatus = (typeof STATUSES)[number];

export const STATUS_LABELS: Record<JobStatus, string> = {
  found: "Found",
  shortlisted: "Shortlisted",
  cv_ready: "CV ready",
  staged: "Staged",
  applied: "Applied",
  interview: "Interview",
  rejected: "Rejected",
  closed: "Closed",
  skipped: "Skipped",
};

export const isStatus = (value: string): value is JobStatus =>
  (STATUSES as readonly string[]).includes(value);

/** "cv_ready" is "CV ready"; an unknown "on_hold" becomes "On hold". */
export function statusLabel(status: string): string {
  if (isStatus(status)) return STATUS_LABELS[status];
  const words = status.replace(/[_\s]+/g, " ").trim();
  if (!words) return "Unknown";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Badge colours per status: muted for the open pipeline, green for progress, red for a no. */
export function statusTone(status: string): string {
  switch (status) {
    case "found":
      return "border-border bg-secondary text-secondary-foreground";
    case "shortlisted":
      return "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300";
    case "cv_ready":
      return "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300";
    case "staged":
      return "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300";
    case "applied":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
    case "interview":
      return "border-success/40 bg-success text-success-foreground";
    case "rejected":
      return "border-destructive/30 bg-destructive/10 text-destructive";
    case "closed":
    case "skipped":
      return "border-border bg-transparent text-muted-foreground";
    default:
      return "border-border bg-transparent text-foreground";
  }
}

export const CONTRACT_TYPES = [
  "CDI",
  "CDD",
  "VIE",
  "Stage",
  "Alternance",
  "Freelance",
] as const;
export type ContractType = (typeof CONTRACT_TYPES)[number];

export const isContractType = (value: string): value is ContractType =>
  (CONTRACT_TYPES as readonly string[]).includes(value);

export const SORTS = [
  "found_on",
  "fit",
  "company",
  "status",
  "updated",
] as const;
export type JobSort = (typeof SORTS)[number];
export const DEFAULT_SORT: JobSort = "found_on";

export const SORT_LABELS: Record<JobSort, string> = {
  found_on: "Newest found",
  fit: "Best fit",
  company: "Company, A to Z",
  status: "Pipeline order",
  updated: "Last changed",
};

export const isSort = (value: string): value is JobSort =>
  (SORTS as readonly string[]).includes(value);

export type FitTone = "top" | "good" | "fair" | "low" | "none";

/** 85 and up is a strong match (the bar stage.py used), 70 good, 50 fair, below that low. */
export function fitTone(fit: number | null | undefined): FitTone {
  if (fit === null || fit === undefined) return "none";
  if (fit >= 85) return "top";
  if (fit >= 70) return "good";
  if (fit >= 50) return "fair";
  return "low";
}

export const FIT_TONE_CLASSES: Record<FitTone, string> = {
  top: "border-success/40 bg-success text-success-foreground",
  good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  fair: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  low: "border-destructive/30 bg-destructive/10 text-destructive",
  none: "border-border bg-transparent text-muted-foreground",
};

/** Bar fill of the histogram bucket that starts at `low`, on the same scale as the badges. */
export function bucketTone(low: number): string {
  const tone = fitTone(low);
  return {
    top: "bg-success",
    good: "bg-emerald-500/70",
    fair: "bg-amber-500/70",
    low: "bg-destructive/60",
    none: "bg-muted-foreground/40",
  }[tone];
}
