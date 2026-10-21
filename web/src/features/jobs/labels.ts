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

// Browser B8: the job sites of a sweep read in the person's browser (sweep-sources.tsx).
const KIND_NAMES: Record<string, string> = {
  google_careers: "Google Careers",
  vie: "Business France VIE",
  site: "Company site",
  linkedin: "LinkedIn",
};

/** What each `error` of a source means, for the person. */
export const SOURCE_PROBLEMS: Record<string, string> = {
  layout_changed:
    "The page looked different from what the app knows, so nothing was kept from it.",
  browser_busy:
    "Your browser was in use, so this site waits for the next search.",
  lease_ended:
    "You took your browser back, so this site stopped. What was read is kept, the rest comes at the next search.",
  time_budget: "The search ran out of time. What was read is kept.",
  lease_budget:
    "The browser's time for this site ran out. What was read is kept, the rest comes at the next search.",
  store_failed:
    "What this site found could not be saved, so nothing was kept from it.",
  page_failed: "A page did not load. What was read before is kept.",
  browser_unavailable: "The browser could not be reached.",
  not_started: "not read this time, it comes at the next search.",
  // LinkedIn (browser B10, app/browser/linkedin_guard.py).
  linkedin_off: "LinkedIn is off for you, so it was not searched.",
  linkedin_paused:
    "LinkedIn showed a security check, so all LinkedIn work is paused for a week. It was not searched.",
  linkedin_daily:
    "LinkedIn was already searched today. It is searched again tomorrow.",
  linkedin_signin:
    "LinkedIn needs you to sign in. Nothing else was opened on LinkedIn.",
  linkedin_checkpoint:
    "LinkedIn showed a security check, so all LinkedIn work is paused for a week.",
};

export function sourceName(source: {
  kind: string;
  query: string;
  location?: string | null;
}): string {
  const parts = [KIND_NAMES[source.kind] ?? source.kind, source.query];
  if (source.location) parts.push(source.location);
  return parts.join(", ");
}

/** A browser source as the sweep names it ("<kind>:<query>[/<location>]": its `current_board`
 * while it runs, and the `source` of the postings it found), in words; any other name as it is.
 * A posting found by several sources names each, joined by ", " (browser B10, sweep.SOURCES_SEP):
 * each is read on its own and the names are joined by "; ". */
export function sourceLabel(current: string): string {
  const names = current.split(/, (?=(?:[a-z_]+):)/);
  return names.length > 1
    ? names.map(oneSource).join("; ")
    : oneSource(current);
}

function oneSource(current: string): string {
  const match = /^(google_careers|vie|site|linkedin):(.*)$/s.exec(current);
  if (!match) return current;
  const [, kind = "", rest = ""] = match;
  const slash = rest.indexOf("/");
  return slash < 0
    ? sourceName({ kind, query: rest })
    : sourceName({
        kind,
        query: rest.slice(0, slash),
        location: rest.slice(slash + 1),
      });
}
