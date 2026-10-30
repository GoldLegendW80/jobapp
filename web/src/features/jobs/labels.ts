/**
 * The vocabulary of the jobs module as the user reads it: statuses in pipeline order with their
 * labels, contract types, sort orders, and the colour tone of a fit score. A value the api adds
 * later and this build does not know still gets a readable label (see `statusLabel`).
 */
import { feedSourceName } from "@/features/opportunities/sources/source-words";

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
  // The job websites searched in the person's browser (jobs J10, mode llm_browser).
  welcome_to_the_jungle: "Welcome to the Jungle",
  indeed: "Indeed",
  freework: "Free-Work",
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
    "What this site found could not be saved, so it stopped there. Postings saved before are kept.",
  page_failed: "A page did not load. What was read before is kept.",
  browser_unavailable: "The browser could not be reached.",
  not_started: "not read this time, it comes at the next search.",
  // LinkedIn (browser B10, app/browser/linkedin_guard.py).
  linkedin_off: "LinkedIn is off for you, so it was not searched.",
  linkedin_paused:
    "LinkedIn showed a security check, so all LinkedIn work is paused for a week. It was not searched.",
  // Sweeps before 2026-10-28 only: no website has a daily limit since (owner).
  linkedin_daily:
    "LinkedIn had already been searched that day, so it was not searched.",
  linkedin_signin:
    "LinkedIn needs you to sign in. Nothing else was opened on LinkedIn.",
  linkedin_checkpoint:
    "LinkedIn showed a security check, so all LinkedIn work is paused for a week.",
  // LinkedIn's public job search (owner, 2026-10-30, app/browser/sources/linkedin_guest.py).
  rate_limited:
    "LinkedIn asked the app to slow down, so its search stopped for this time. Postings saved before are kept.",
  linkedin_searched_once:
    "LinkedIn was searched once through its public job search for all your LinkedIn addresses.",
  // The job websites searched in the person's browser (jobs J10, app/browser/sources/interactive.py).
  permission_unavailable:
    "Searching this website needs your permission for it, so it was not opened. Tick it in Find jobs and save to give it.",
  permission_revoked:
    "Your permission for this website was turned off during the search, so it stopped. What was read is kept.",
  permission_checkpoint:
    "This website showed a security check, so it is paused for a week. It was not searched further.",
  // Sweeps before 2026-10-28 only, as linkedin_daily.
  platform_daily:
    "This website had already been searched that day, so it was not searched.",
  platform_signin:
    "This website needs you to sign in. Use Sign in beside it in Find jobs, then search again. Nothing else was opened on it.",
  // Sweeps before jobs J11 (#632) counted the postings of a website without saving them.
  finding_persistence_pending:
    "Postings were read on this website, but saving them came with a later update.",
  unsupported_interaction:
    "The website asked for something the app does not do, so the app refused it. Postings saved before are kept.",
  // browserd could not close a tab off the website's list or take the page back from an
  // address off it (its `guard_failed`), so the website ends there (round 2 of PR 720).
  guard_failed:
    "The browser could not take back a page outside this website, so the search of it stopped. Postings saved before are kept.",
  unsupported_criteria:
    "Your search uses settings this website search does not take yet, so it was not opened.",
  // How a website's agent ended with no posting: what it saw, said in its own words
  // (2026-10-28, interactive.outcome in app/browser/sources/interactive.py).
  no_job_list:
    "This website showed no list of jobs, only a count or a prompt to create an account. Use Sign in beside it in Find jobs, then search again.",
  cookie_wall:
    "A cookie banner on this website could not be declined, so it was not searched.",
  signin_wall:
    "This website shows its jobs only to people signed in. Use Sign in beside it in Find jobs, then search again.",
  no_search_box:
    "The assistant found no search box on this website, so it was not searched.",
  // The app refused the agent's typing, Enter or search button, or an address it opened, and
  // nothing was found: the refusal wins over the agent's own reason (B19, 2026-10-29).
  search_refused: "The app refused the search on this website.",
  // The website showed a security check (Cloudflare's) that did not clear while the agent
  // waited (2026-10-30, Indeed).
  security_check:
    "The website showed a security check the app's browser could not pass.",
  nothing_reported:
    "The assistant read this website but reported no job and no reason. Postings saved before are kept.",
  no_credential: "Add your model access in Account to search this website.",
  agent_failed:
    "The assistant stopped before the end. Postings saved before are kept.",
  // A company careers page's page cap (app/browser/finder.py), or LinkedIn's in sweeps before
  // 2026-10-28; the agent of a job website has no cap (owner, 2026-10-27 and 2026-10-28).
  page_budget:
    "The app read as many pages of this website as it may this time. What was read is kept.",
  // A company careers page's run (browser B9).
  token_budget:
    "The assistant used all it may read on this site this time. What was read is kept.",
};

/** The searches the agent of a website ran (jobs J18, owner 2026-10-27), from its own tool
 * calls: how many, and which, each a saved title, in the saved place it chose if any. Empty
 * when it ran none or the api is older. */
export function searchesSentence(source: {
  searches?: number;
  searched?: { title: string; place: string | null }[];
}): string {
  const ran = source.searches ?? 0;
  if (ran <= 0) return "";
  const head = `${ran.toLocaleString("en-GB")} ${ran === 1 ? "search" : "searches"} ran`;
  const named = (source.searched ?? []).map((one) =>
    one.place ? `${one.title} in ${one.place}` : one.title,
  );
  if (!named.length) return `${head}.`;
  const more = ran - named.length;
  const rest = more > 0 ? `, and ${more.toLocaleString("en-GB")} more` : "";
  return `${head}: ${named.join("; ")}${rest}.`;
}

/** The saved titles and places a website's search could not carry (jobs J18): its scope takes
 * 100 of each and 32 KiB in all, so a longer list is searched by its first ones, in saved order,
 * and the rest is told here, with a pattern an earlier version saved that holds no plain word
 * (app/profile/plain_terms.py). Empty when none was left out or the api is older. */
export function leftOutSentence(source: {
  titles_left_out?: number;
  places_left_out?: number;
}): string {
  const count = (n: number, one: string, many: string) =>
    `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
  const parts = [
    source.titles_left_out
      ? count(source.titles_left_out, "saved title", "saved titles")
      : "",
    source.places_left_out
      ? count(source.places_left_out, "saved place", "saved places")
      : "",
  ].filter((part) => part !== "");
  if (!parts.length) return "";
  const total = (source.titles_left_out ?? 0) + (source.places_left_out ?? 0);
  const verb = total === 1 ? "was" : "were";
  return `${parts.join(" and ")} ${verb} left out: a website's search types plain words, the first ones that fit.`;
}

export function sourceName(source: {
  kind: string;
  query: string;
  location?: string | null;
  host?: string | null;
}): string {
  // A website of the person's own (jobs J9, platform custom) is named by its host.
  const kind =
    source.kind === "custom" && source.host
      ? source.host
      : (KIND_NAMES[source.kind] ?? source.kind);
  const parts = source.query ? [kind, source.query] : [kind];
  if (source.location) parts.push(source.location);
  return parts.join(", ");
}

/** A browser source as the sweep names it ("<kind>:<query>[/<location>]": its `current_board`
 * while it runs, and the `source` of the postings it found), in words; any other name as it is.
 * A posting found by several sources names each, joined by ", " (browser B10, sweep.SOURCES_SEP):
 * each is read on its own and the names are joined by "; ". A feed's source has its name too
 * ("remoteok" is "Remote OK", opportunities O8). */
export function sourceLabel(current: string): string {
  const names = current.split(
    /, (?=[a-z_]+:|(?:remoteok|weworkremotely)(?:, |$))/,
  );
  return names.length > 1
    ? names.map(oneSource).join("; ")
    : oneSource(current);
}

function oneSource(current: string): string {
  // Opportunities O8: a freelance and project feed ("remoteok", "hackernews:freelance").
  const feed = feedSourceName(current);
  if (feed) return feed;
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
