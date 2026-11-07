/**
 * Where a search looks, as one list of addresses (owner, 2026-11-04: "an url is an url"). The
 * person pastes any address; the app picks how to read it:
 *
 * - a job website the app knows (LinkedIn, Welcome to the Jungle, Indeed, Free-Work): searched by
 *   the assistant, with that website's own permission and risk sentence (jobs J17);
 * - a job feed (Arbeitnow, Himalayas, Adzuna): read through its official API (app/jobs/feeds);
 * - a company job board on Ashby, Greenhouse, Lever, SmartRecruiters, Workable or Recruitee:
 *   read through its public API (app/jobs/boards, POST /api/jobs/boards/detect names it);
 * - any other address: a website of the person's own, browsed by the assistant.
 *
 * The profile keeps the three stores it always had (`interactive_sites`, `job_feeds`,
 * `company_boards`), so a search reads them as before. This module is pure: the editor's hook
 * (use-sources-editor.ts) calls the api for the boards.
 */
import {
  interactiveSettingsSchema,
  type CompanyBoard,
  type InteractiveSite,
  type Search,
} from "@/features/profile/api/profile-api";

import { ATS_NAMES, BOARDS_MAX, boardKey, boardPage } from "../api/boards-api";
import { FEED_NAMES, JOB_FEEDS, isJobFeed, type JobFeed } from "./job-feeds";
import { platformOf, type Protected } from "./site-permissions";

/** schemas.py `interactive_sites` max_length. */
export const SITES_MAX = 10;

export interface SourcesDraft {
  sites: InteractiveSite[];
  /** null: never saved; the search reads no company board until the person adds one. */
  boards: CompanyBoard[] | null;
  feeds: JobFeed[];
}

/** The list as a search section stores it. */
export function draftOfSearch(search: Search): SourcesDraft {
  return {
    sites: structuredClone(search.interactive_sites ?? []),
    boards: search.company_boards
      ? structuredClone(search.company_boards)
      : null,
    feeds: (search.job_feeds ?? []).filter(isJobFeed),
  };
}

export const PLATFORM_NAMES: Record<Protected, string> = {
  linkedin: "LinkedIn",
  welcome_to_the_jungle: "Welcome to the Jungle",
  indeed: "Indeed",
  freework: "Free-Work",
};

/** The address each known job website starts from, as Find jobs always saved it. */
export const PLATFORM_URLS: Record<Protected, string> = {
  linkedin: "https://www.linkedin.com/",
  welcome_to_the_jungle: "https://www.welcometothejungle.com/en/jobs",
  indeed: "https://www.indeed.com/",
  freework: "https://www.free-work.com/",
};

export const FEED_URLS: Record<JobFeed, string> = {
  arbeitnow: "https://www.arbeitnow.com/",
  himalayas: "https://himalayas.app/",
  adzuna: "https://www.adzuna.fr/",
};

/** What each feed covers, said once in its row. */
export const FEED_SCOPES: Record<JobFeed, string> = {
  arbeitnow: "Jobs in Europe",
  himalayas: "Remote jobs open to France",
  adzuna: "Jobs in France",
};

export interface Example {
  label: string;
  url: string;
  /** The known job website the example adds, saved under its own platform as Find jobs always did. */
  platform?: Protected;
}

/** The examples offered under the address box: one click adds them. */
export const EXAMPLES: readonly Example[] = [
  { label: "LinkedIn", url: PLATFORM_URLS.linkedin, platform: "linkedin" },
  {
    label: "Welcome to the Jungle",
    url: PLATFORM_URLS.welcome_to_the_jungle,
    platform: "welcome_to_the_jungle",
  },
  { label: "Indeed", url: PLATFORM_URLS.indeed, platform: "indeed" },
  { label: "Free-Work", url: PLATFORM_URLS.freework, platform: "freework" },
  { label: "Arbeitnow", url: FEED_URLS.arbeitnow },
  { label: "Himalayas", url: FEED_URLS.himalayas },
  { label: "Doctolib on Ashby", url: "https://jobs.ashbyhq.com/doctolib" },
  {
    label: "Datadog on Greenhouse",
    url: "https://job-boards.greenhouse.io/datadog",
  },
];

/** The host of an address, lowercased and without its final dot, or null. */
function hostOf(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:")
      return null;
    return parsed.hostname.toLowerCase().replace(/\.$/, "");
  } catch {
    return null;
  }
}

const under = (host: string, root: string) =>
  host === root || host.endsWith(`.${root}`);

/** "linkedin.com/jobs" -> "https://linkedin.com/jobs": an address typed without its scheme. */
export function withScheme(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
}

/** The job feed an address names (its own website), or null. */
export function feedOf(url: string): JobFeed | null {
  const host = hostOf(url);
  if (!host) return null;
  if (under(host, "arbeitnow.com")) return "arbeitnow";
  if (under(host, "himalayas.app")) return "himalayas";
  // Adzuna has a site per country (adzuna.fr, adzuna.co.uk...): any of them is the feed.
  if (host.split(".").includes("adzuna")) return "adzuna";
  return null;
}

/**
 * The recommended places, added together by one button (click analysis A1): the known job
 * websites and the two job feeds of EXAMPLES, never a company board example. Each known job
 * website keeps its risk sentence in its line, and its permission is still given at Save (J17).
 */
export const RECOMMENDED: readonly Example[] = EXAMPLES.filter(
  (example) => example.platform !== undefined || feedOf(example.url) !== null,
);

/** The recommended places the draft does not list yet (a known website under any address). */
export function missingRecommended(draft: SourcesDraft): Example[] {
  return RECOMMENDED.filter((example) =>
    example.platform
      ? !draft.sites.some((site) => platformOf(site) === example.platform)
      : !listed(draft, example.url),
  );
}

/** The draft with every recommended place it lacks, the websites within SITES_MAX: the names
 * added, and those left out for want of room. */
export function withRecommended(draft: SourcesDraft): {
  draft: SourcesDraft;
  added: string[];
  left: string[];
} {
  let { sites, feeds } = draft;
  const added: string[] = [];
  const left: string[] = [];
  for (const example of missingRecommended(draft)) {
    const feed = feedOf(example.url);
    if (feed) {
      feeds = withFeed(feeds, feed);
      added.push(example.label);
      continue;
    }
    const checked = checkSite(sites, siteOf(example.url, example.platform));
    if ("error" in checked) {
      left.push(example.label);
      continue;
    }
    sites = checked.sites;
    added.push(example.label);
  }
  return { draft: { ...draft, sites, feeds }, added, left };
}

/** "LinkedIn, Indeed and Himalayas". */
export function namesSentence(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""}`;
}

/** A website of the list, checked as the api checks it; the message to show when it is not. */
export function checkSite(
  sites: readonly InteractiveSite[],
  site: InteractiveSite,
): { sites: InteractiveSite[] } | { error: string } {
  if (sites.length >= SITES_MAX)
    return {
      error: `Keep at most ${String(SITES_MAX)} websites. Remove one to add another.`,
    };
  const parsed = interactiveSettingsSchema.safeParse({
    interactive_sites: [...sites, site],
    interactive_instructions: "",
  });
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      error:
        issue?.message === "This website is already selected."
          ? "This website is already in your list."
          : "Check this address: use a public website, such as https://www.example.com/jobs.",
    };
  }
  return { sites: parsed.data.interactive_sites };
}

/** The address as a website of the list. An address the person typed is a website of her own
 * (`custom`), as before: one at a known job website still needs that website's permission
 * (`platformOf`). The examples of the known job websites keep their platform. */
export function siteOf(url: string, platform?: Protected): InteractiveSite {
  return {
    platform: platform ?? "custom",
    url,
    mode: "llm_browser",
    enabled: true,
  };
}

/** Whether the format of what the person typed can be an address at all (no api call). */
export function addressProblem(text: string): string | null {
  const url = withScheme(text);
  if (!url) return "Paste or type an address first.";
  if (/\s/.test(url)) return "An address has no spaces.";
  const host = hostOf(url);
  if (!host || !host.includes(".") || host.startsWith("."))
    return "This is not a website address. Try something like https://www.example.com/jobs.";
  return null;
}

/** Adds a feed once, in the feeds' own order. */
export function withFeed(feeds: readonly JobFeed[], feed: JobFeed): JobFeed[] {
  const next = new Set([...feeds, feed]);
  return JOB_FEEDS.filter((name) => next.has(name));
}

/** Adds a board once, at the end; the message when it cannot be added. */
export function withBoard(
  boards: readonly CompanyBoard[],
  board: CompanyBoard,
): { boards: CompanyBoard[] } | { error: string } {
  if (boards.some((item) => boardKey(item) === boardKey(board)))
    return { error: `${board.company} is already in your list.` };
  if (boards.length >= BOARDS_MAX)
    return {
      error: `Keep at most ${String(BOARDS_MAX)} company boards. Remove one to add another.`,
    };
  return { boards: [...boards, { ...board, enabled: true }] };
}

export type RowKind = "website" | "feed" | "board";

/** One line of the list, whatever store it comes from. */
export interface SourceRow {
  key: string;
  kind: RowKind;
  name: string;
  /** The address shown under the name and opened by its link. */
  url: string;
  /** How the app reads it, in a few plain words. */
  how: string;
  enabled: boolean;
  /** The known job website whose permission this line needs, if any. */
  platform: Protected | null;
  /** Index in its own store. */
  index: number;
  feed?: JobFeed;
}

function shortAddress(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${parsed.host.replace(/^www\./, "")}${path}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}

/** "https://www.example.com/careers/" -> "example.com/careers": the name an added address goes by. */
export function shortName(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
}

/** Every line of the list: the websites, then the feeds, then the company boards. */
export function rowsOf(draft: SourcesDraft): SourceRow[] {
  const sites = draft.sites.map((site, index): SourceRow => {
    const platform = platformOf(site);
    const known = site.platform !== "custom" ? site.platform : null;
    return {
      // The api refuses two websites at one address, so the address names the line.
      key: `site:${site.url}`,
      kind: "website",
      name: known ? PLATFORM_NAMES[known] : shortAddress(site.url),
      url: site.url,
      how: known
        ? "Job website, searched by the assistant"
        : "Website of your choice, browsed by the assistant",
      enabled: site.enabled,
      platform,
      index,
    };
  });
  const feeds = draft.feeds.map((feed, index): SourceRow => ({
    key: `feed:${feed}`,
    kind: "feed",
    name: FEED_NAMES[feed],
    url: FEED_URLS[feed],
    how: `${FEED_SCOPES[feed]}, read through its official feed`,
    enabled: true,
    platform: null,
    index,
    feed,
  }));
  const boards = (draft.boards ?? []).map((board, index): SourceRow => ({
    key: `board:${boardKey(board)}`,
    kind: "board",
    name: board.company,
    url: boardPage(board),
    how: `Company job board on ${ATS_NAMES[board.ats]}, read directly`,
    enabled: board.enabled,
    platform: null,
    index,
  }));
  return [...sites, ...feeds, ...boards];
}

/** How many places a search of this draft reads. */
export function sourceCount(draft: SourcesDraft): number {
  return (
    draft.sites.filter((site) => site.enabled).length +
    draft.feeds.length +
    (draft.boards ?? []).filter((board) => board.enabled).length
  );
}

/** The draft without the line `row`. */
export function withoutRow(draft: SourcesDraft, row: SourceRow): SourcesDraft {
  if (row.kind === "website")
    return {
      ...draft,
      sites: draft.sites.filter((_, at) => at !== row.index),
    };
  if (row.kind === "feed")
    return { ...draft, feeds: draft.feeds.filter((feed) => feed !== row.feed) };
  return {
    ...draft,
    boards: (draft.boards ?? []).filter((_, at) => at !== row.index),
  };
}

/** The draft with the paused line `row` searched again. */
export function resumed(draft: SourcesDraft, row: SourceRow): SourcesDraft {
  if (row.kind === "website")
    return {
      ...draft,
      sites: draft.sites.map((site, at) =>
        at === row.index ? { ...site, enabled: true } : site,
      ),
    };
  if (row.kind === "board")
    return {
      ...draft,
      boards: (draft.boards ?? []).map((board, at) =>
        at === row.index ? { ...board, enabled: true } : board,
      ),
    };
  return draft;
}

/** Whether the address is already a line of the draft (any store). */
export function listed(draft: SourcesDraft, url: string): boolean {
  const feed = feedOf(url);
  if (feed) return draft.feeds.includes(feed);
  const key = canonicalKey(url);
  return draft.sites.some((site) => canonicalKey(site.url) === key);
}

function canonicalKey(url: string): string {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    parsed.hostname = parsed.hostname.replace(/\.$/, "");
    return parsed.href;
  } catch {
    return url;
  }
}
