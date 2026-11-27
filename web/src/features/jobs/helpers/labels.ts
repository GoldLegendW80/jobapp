/**
 * The vocabulary of the jobs module as the user reads it: statuses in pipeline order with their
 * labels, contract types, sort orders, and the colour tone of a fit score. A value the api adds
 * later and this build does not know still gets a readable label (see `statusLabel`).
 */
import { feedSourceName } from "@/features/opportunities/helpers/sources/source-words";

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
      return "border-info/30 bg-info/10 text-info";
    case "cv_ready":
      return "border-transparent bg-highlight text-highlight-foreground";
    case "staged":
      return "border-warning/30 bg-warning/10 text-warning";
    case "applied":
      return "border-success/30 bg-success/10 text-success";
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
  "published",
  "pay",
] as const;
export type JobSort = (typeof SORTS)[number];
export const DEFAULT_SORT: JobSort = "found_on";

export const SORT_LABELS: Record<JobSort, string> = {
  found_on: "Newest found",
  fit: "Best fit",
  company: "Company, A to Z",
  status: "Pipeline order",
  updated: "Last changed",
  published: "Newest published",
  pay: "Best pay, yearly first",
};

/** The work mode filter: a stated mode, or none stated (api `work_mode`). */
export const WORK_MODE_CHOICES = [
  "remote",
  "hybrid",
  "office",
  "not_stated",
] as const;
export type WorkModeChoice = (typeof WORK_MODE_CHOICES)[number];
export const WORK_MODE_CHOICE_LABELS: Record<WorkModeChoice, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  office: "On site",
  not_stated: "Not stated",
};

/** The application filter: what a posting's newest application says (api `application`). */
export const APPLICATION_CHOICES = [
  "none",
  "under_way",
  "waiting",
  "sent",
  "failed",
] as const;
export type ApplicationChoice = (typeof APPLICATION_CHOICES)[number];
export const APPLICATION_CHOICE_LABELS: Record<ApplicationChoice, string> = {
  none: "No application",
  under_way: "Applying",
  waiting: "Waiting for you",
  sent: "Sent",
  failed: "Failed",
};

export const PAY_PERIODS = ["year", "month", "day", "hour"] as const;
export type PayPeriodChoice = (typeof PAY_PERIODS)[number];

const WEBSITE_NAMES: Record<string, string> = {
  mail: "Job alert e-mails",
  manual: "Added by hand",
  import: "Imported",
};

/** A website of the facets (a source name before ":", or a host) in words. */
export const websiteName = (value: string): string =>
  KIND_NAMES[value] ?? WEBSITE_NAMES[value] ?? value;

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
  good: "border-success/30 bg-success/10 text-success",
  fair: "border-warning/30 bg-warning/10 text-warning",
  low: "border-destructive/30 bg-destructive/10 text-destructive",
  none: "border-border bg-transparent text-muted-foreground",
};

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
  // A company job board read through its ATS's public API (owner, 2026-10-30).
  board: "Company board",
  // The search of the whole web (jobs J21, mode web): its postings' source is "web".
  web: "Anywhere on the web",
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
  // Company job boards (owner, 2026-10-30, app/jobs/boards/client.py and run.py).
  not_found:
    "This board was not found on its ATS. Check its address in Find jobs.",
  blocked: "The ATS refused to answer for this board.",
  rate_limited:
    "The ATS asked the app to slow down, so its other boards wait for the next search.",
  host_rate_limited:
    "Its ATS asked the app to slow down earlier in this search, so this board waits for the next one.",
  upstream_error:
    "The ATS answered with an error. The board is read again at the next search.",
  timeout: "The ATS did not answer in time.",
  network: "The ATS could not be reached.",
  too_large: "The board's answer was too large to read.",
  invalid_response: "The ATS answered something the app could not read.",
  no_titles:
    "Add a title you want in Find jobs: a company board is never read whole.",
  store_refused:
    "What this board found could not be saved, so it stopped there. Postings saved before are kept.",
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
  linkedin_rate_limited:
    "LinkedIn asked the app to slow down, so its search stopped for this time. Postings saved before are kept.",
  linkedin_searched_once:
    "LinkedIn was searched once through its public job search for all your LinkedIn addresses.",
  // Its public page read in the browser after the public job search refused (jobs J22).
  linkedin_page_refused:
    "LinkedIn showed a sign in page or asked to slow down on its public job search page, so its search stopped for this time. Postings saved before are kept.",
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
  // The browser's memory guard closed the website's tab three times in one run (browser B29,
  // finder.MEMORY_CLOSES_MAX), so the website ends there and gets the sweep's one retry.
  browser_memory:
    "The browser ran short of memory again and again on this website, so its search stopped. Postings saved before are kept.",
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
  // Anywhere on the web: Claude Code's web search failed twice in a row before the plan of
  // searches was done (jobs J23, round 1 of #814), never "searched, nothing new".
  web_search_failed:
    "The web search failed twice in a row, so the searches planned were not all made. Postings saved before are kept.",
  // Anywhere on the web: the assistant stopped before the searches planned were all made, and
  // twice more after the app asked it to go on (jobs J27). What it reported is kept.
  plan_unfinished:
    "The assistant stopped before it made every search planned, even after the app asked it twice to go on. What it found is kept.",
  no_credential: "Add your model access in Account to search this website.",
  agent_failed:
    "The assistant stopped before the end. Postings saved before are kept.",
  // The person cancelled the website's run in Activity (browser B24): never searched again.
  agent_cancelled:
    "You cancelled the search of this website. Postings saved before are kept.",
  // A company careers page's page cap (app/browser/finder.py), or LinkedIn's in sweeps before
  // 2026-10-28; the agent of a job website has no cap (owner, 2026-10-27 and 2026-10-28).
  page_budget:
    "The app read as many pages of this website as it may this time. What was read is kept.",
  // A company careers page's run (browser B9).
  token_budget:
    "The assistant used all it may read on this site this time. What was read is kept.",
};

/** LinkedIn's public job search stopped by its own time cap (its `time_budget`, 2026-10-31):
 * LinkedIn is read about one request every 10 seconds. */
export const LINKEDIN_TIME_BUDGET =
  "LinkedIn is read slowly, about one request every 10 seconds, so its time in this search ran out. What was read is kept.";

/** The searches the agent of a website ran (jobs J18, owner 2026-10-27), from its own tool
 * calls: how many, and which, each a saved title, in the saved place it chose if any. Empty
 * when it ran none or the api is older. LinkedIn's public job search also says how many it
 * planned when its caps stopped it before the end ("11 of 21 searches ran", 2026-10-31). */
export function searchesSentence(source: {
  searches?: number;
  searched?: { title: string; place: string | null }[];
  planned?: number;
}): string {
  const ran = source.searches ?? 0;
  if (ran <= 0) return "";
  const planned = source.planned ?? 0;
  const count =
    planned > ran
      ? `${ran.toLocaleString("en-GB")} of ${planned.toLocaleString("en-GB")}`
      : ran.toLocaleString("en-GB");
  const head = `${count} ${(planned > ran ? planned : ran) === 1 ? "search" : "searches"} ran`;
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

/** LinkedIn's public job search (jobs J20, owner 2026-11-17): when it stood in for the browser
 * search, when its public page in the browser stood in for it (jobs J22), the postings of other places it left out, and the saved pairs it did not search.
 * Empty when none applies or the api is older. */
export function guestSentence(source: {
  fallback?: string | null;
  fallback_after?: string | null;
  other_place?: number;
  not_searched?: { title: string; place: string | null }[];
}): string {
  const parts: string[] = [];
  if (source.fallback === "guest") {
    parts.push(
      source.fallback_after === "linkedin_signin"
        ? "Searched with LinkedIn's public job search, as you are not signed in to LinkedIn."
        : "Searched with LinkedIn's public job search, as the browser search failed.",
    );
  }
  if (source.fallback === "page") {
    parts.push(
      source.fallback_after === "linkedin_rate_limited"
        ? "Searched with LinkedIn's public job search page, as the public API refused."
        : "Searched with LinkedIn's public job search page, as the public API failed.",
    );
  }
  const other = source.other_place ?? 0;
  if (other > 0) {
    const what = other === 1 ? "posting" : "postings";
    parts.push(
      `${other.toLocaleString("en-GB")} ${what} of a place you did not choose left out.`,
    );
  }
  const left = (source.not_searched ?? []).map((one) =>
    one.place ? `${one.title} in ${one.place}` : one.title,
  );
  if (left.length) parts.push(`Not searched this time: ${left.join("; ")}.`);
  return parts.join(" ");
}

/** Browser B29: the times the website's tab was closed and opened again, each by the reason the
 * app knew (the browser's memory guard, a crash of the page, else none it saw), and whether the
 * search then left out images, videos and fonts. Empty when none or the api is older. */
export function tabClosedSentence(source: {
  tab_closed?: number;
  tab_closed_reasons?: Record<string, number>;
  tab_closed_mib?: number[];
  media_blocked?: boolean;
}): string {
  const total = source.tab_closed ?? 0;
  if (total <= 0) return "";
  const times = (n: number) =>
    n === 1 ? "once" : n === 2 ? "twice" : `${n.toLocaleString("en-GB")} times`;
  const mib = (source.tab_closed_mib ?? []).map(
    (n) => `${n.toLocaleString("en-GB")} MiB`,
  );
  const phrases: Record<string, string> = {
    memory: `as the browser's memory ran short${mib.length ? `, at ${mib.join(" and ")}` : ""}`,
    crash: "as the page crashed",
    other: "for a reason the app did not see",
  };
  const listed = Object.entries(source.tab_closed_reasons ?? {}).filter(
    ([reason, n]) => n > 0 && Object.hasOwn(phrases, reason),
  );
  const only = listed.length === 1 && listed[0]?.[1] === total;
  const why = only
    ? ` ${phrases[listed[0]?.[0] ?? ""] ?? ""}`
    : listed.length
      ? ` (${listed.map(([reason, n]) => `${times(n)} ${phrases[reason] ?? ""}`).join("; ")})`
      : "";
  const media = source.media_blocked
    ? ", and the search then loaded no images, videos or fonts"
    : "";
  return `The website's tab was closed and opened again ${times(total)}${why}${media}.`;
}

export function sourceName(source: {
  kind: string;
  query: string;
  location?: string | null;
  host?: string | null;
  company?: string | null;
}): string {
  // A provider's job alert e-mails ("mail:indeed"), SPEC-mail.md "Job alert e-mails".
  const alerts = alertSourceName(source.host ?? "");
  if (alerts) return alerts;
  // A website of the person's own (jobs J9, platform custom) is named by its host.
  const kind =
    source.kind === "custom" && source.host
      ? source.host
      : (KIND_NAMES[source.kind] ?? source.kind);
  // A company job board by its company (owner, 2026-10-30), else its `<ats>:<board>`.
  const query =
    source.kind === "board" && source.company ? source.company : source.query;
  const parts = query ? [kind, query] : [kind];
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
    /, (?=[a-z_]+:|(?:remoteok|weworkremotely|arbeitnow|himalayas|adzuna|freehire|web)(?:, |$))/,
  );
  return names.length > 1
    ? names.map(oneSource).join("; ")
    : oneSource(current);
}

const ALERT_SOURCES = new Map([
  ["mail:indeed", "Indeed alert e-mails"],
  ["mail:linkedin", "LinkedIn alert e-mails"],
]);

/** The words of a job alert e-mails source ("mail:indeed"), null for any other name. */
export function alertSourceName(current: string): string | null {
  return ALERT_SOURCES.get(current) ?? null;
}

function oneSource(current: string): string {
  const alerts = alertSourceName(current);
  if (alerts) return alerts;
  // Opportunities O8: a freelance and project feed ("remoteok", "hackernews:freelance").
  const feed = feedSourceName(current);
  if (feed) return feed;
  // Jobs J21: a posting "Anywhere on the web" found.
  if (current === "web") return KIND_NAMES.web ?? current;
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
