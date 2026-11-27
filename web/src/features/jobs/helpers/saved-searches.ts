/** The words of Your searches (find jobs, jobs J33): what a search looks for, when it runs, its
 * last run, what a run did, what a save that stopped halfway left, and the websites the other
 * searches keep a permission for. */
import { describeSchedule } from "@/features/profile/helpers/schedule";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import type { Criteria, SavedSearch, SearchRun } from "../api/searches-api";
import type { ScheduleState, Sweep } from "../api/sweeps-api";
import { platformOf, tickedOf, type Protected } from "./site-permissions";
import { PLATFORM_NAMES, shortName } from "./sources";
import { plural } from "./selection";
import { weekdays } from "./schedule";

export const NAME_MAX = 60;
const MAIN_NAME = "main search";

function terms(list: { terms?: string[] } | undefined): string[] {
  return (list?.terms ?? []).filter((term) => term.trim());
}

/** The ticked websites by name, then the company boards and the job feeds counted. */
function sourcesWords(criteria: Criteria): string[] {
  const sites = (criteria.interactive_sites ?? [])
    .filter((site) => site.enabled)
    .map((site) => {
      const platform = platformOf(site);
      return platform ? PLATFORM_NAMES[platform] : shortName(site.url);
    });
  const boards = criteria.company_boards?.length ?? 0;
  const feeds = criteria.job_feeds?.length ?? 0;
  return [
    ...sites,
    ...(boards ? [plural(boards, "company board")] : []),
    ...(feeds ? [plural(feeds, "job feed")] : []),
    ...(criteria.web_search === false ? [] : ["the web"]),
  ];
}

/** One line: the titles, the places and where it searches. */
export function criteriaLine(criteria: Criteria): string {
  const titles = terms(criteria.title_include);
  const places = terms(criteria.location);
  return [
    titles.length ? titles.join(", ") : "Any title",
    places.length ? places.join(", ") : "Anywhere",
    `on ${sourcesWords(criteria).join(", ")}`,
  ].join("; ");
}

export function scheduleWords(schedule: ScheduleState): string {
  if (!schedule.enabled) return "Runs when you start it";
  const line = `Automatic: ${describeSchedule({
    enabled: true,
    days: weekdays(schedule.days),
    times: [...schedule.times],
    timezone: schedule.timezone,
  }).replace(/^on, /, "")}`;
  return schedule.active ? line : `${line}, nothing to search yet`;
}

/** The search a sweep ran: a sweep from before saved searches ran the main one. */
export function searchOf(sweep: Pick<Sweep, "search">): string | null {
  return sweep.search === undefined || sweep.search === null
    ? "main"
    : sweep.search.id;
}

/** A search's name for the Jobs list's filter: null once the person no longer has it, undefined
 * while her searches are not read. */
export function searchNameOf(
  searches: readonly SavedSearch[] | undefined,
): (id: string) => string | null | undefined {
  return (id) =>
    searches
      ? (searches.find((one) => one.id === id)?.name ?? null)
      : undefined;
}

/** A free name for a copy: "Name copy", then "Name copy 2", within the 60 characters. */
export function copyName(name: string, names: readonly string[]): string {
  const taken = new Set([MAIN_NAME, ...names.map((one) => one.toLowerCase())]);
  for (let number = 1; ; number += 1) {
    const tail = number === 1 ? " copy" : ` copy ${String(number)}`;
    const candidate = `${name.slice(0, NAME_MAX - tail.length).trimEnd()}${tail}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}

/** What is wrong with a name, or null: 1 to 60 characters, not the main one's, not taken. */
export function nameProblem(
  name: string,
  others: readonly string[],
): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Give this search a name.";
  if (trimmed.length > NAME_MAX)
    return `Keep the name within ${String(NAME_MAX)} characters.`;
  if (trimmed.toLowerCase() === MAIN_NAME)
    return "Main search is the name of your main search.";
  if (
    others.some((other) => other.trim().toLowerCase() === trimmed.toLowerCase())
  )
    return "You already have a search with this name.";
  return null;
}

/** Why a run of the api started nothing (searches_router.py, sweep.BeginOutcome). */
const NOT_STARTED: Record<string, string> = {
  no_websites: "It has no job website, company board or job feed to search.",
  search_invalid: "It no longer passes the checks: open it and save it again.",
  queue_down: "The search could not be queued. Try again in a minute.",
  running: "Another search is running.",
  not_found: "It no longer exists.",
};

/** What a run that started no sweep here did: Run now and "Save and start search" say the same. */
export function runWords(
  name: string,
  run: Pick<SearchRun, "outcome" | "request">,
): string {
  if (run.outcome === "queued")
    return run.request
      ? `${name} waits in line: number ${String(run.request.place)}.`
      : `${name} waits in line.`;
  if (run.outcome === "started") return `${name} started.`;
  return `${name} did not start. ${NOT_STARTED[run.outcome] ?? "Try again."}`;
}

export const SEARCH_GONE = "This search no longer exists.";

/** Why a saved search's schedule was not saved, in the words of its form and its row: `gone`
 * when the search no longer exists, so there is nothing to save again; `moved` when it changed in
 * another tab, so it must be read again before the next save. */
export function scheduleFailure(failure: unknown): {
  text: string;
  gone: boolean;
  moved: boolean;
} {
  if (isApiError(failure) && failure.status === 404)
    return { text: SEARCH_GONE, gone: true, moved: false };
  if (
    isApiError(failure) &&
    failure.status === 409 &&
    (failure.body as { revision?: unknown } | null)?.revision !== undefined
  )
    return {
      text: "This search changed in another tab. Try again.",
      gone: false,
      moved: true,
    };
  return {
    text: `Not saved. ${errorDetail(failure) ?? describeError(failure)}`,
    gone: false,
    moved: false,
  };
}

/** "the Indeed permission", "the Indeed and LinkedIn permissions". */
function permissionsWords(platforms: readonly Protected[]): string {
  const names = platforms.map((platform) => PLATFORM_NAMES[platform]);
  return names.length < 2
    ? `the ${names[0] ?? ""} permission`
    : `the ${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""} permissions`;
}

/** The permissions named, then the verb that agrees with them: "the Indeed permission was". */
function permissionsAre(
  platforms: readonly Protected[],
  one: string,
  many: string,
): string {
  return `${permissionsWords(platforms)} ${platforms.length < 2 ? one : many}`;
}

/** A saved search's save that stopped after it changed something (jobs J33). */
export interface HalfSaved {
  name: string;
  /** The permissions this save gave before it stopped. */
  given: readonly Protected[];
  /** Whether the search was written: null when its answer was lost. */
  written: boolean | null;
  /** The permission write that failed; `refused` when its route said no, so nothing changed. */
  failed?: { platform: Protected; refused: boolean };
  /** The permissions to take back that were not tried after the failure. */
  untried: readonly Protected[];
  /** Why it stopped, in whole sentences. */
  reason: string;
  /** Current settings must be loaded before saving again. */
  conflict: boolean;
}

/** What is saved and what is not, in plain words, then what to do. */
export function halfSavedWords(state: HalfSaved): string {
  const { name, given, failed, untried } = state;
  const next = state.conflict
    ? "Load current settings, then save again."
    : state.written
      ? "Try Save again."
      : "";
  let facts: string;
  if (state.written) {
    const done = given.length
      ? `${name} is saved and ${permissionsAre(given, "is", "are")} given`
      : `${name} is saved`;
    const left = !failed
      ? ""
      : failed.refused
        ? `${permissionsAre([failed.platform, ...untried], "was", "were")} not taken back`
        : `taking back ${permissionsWords([failed.platform])} was not confirmed${
            untried.length
              ? `, and ${permissionsAre(untried, "was", "were")} not taken back`
              : ""
          }`;
    facts = left ? `${done}, but ${left}.` : `${done}.`;
  } else {
    const search =
      state.written === null
        ? `${name} may not be saved`
        : `${name} was not saved`;
    const grant = !failed
      ? ""
      : failed.refused
        ? `: ${permissionsWords([failed.platform])} was not given`
        : `: giving ${permissionsWords([failed.platform])} was not confirmed`;
    const head = permissionsAre(given, "is", "are");
    facts = `${head.charAt(0).toUpperCase()}${head.slice(1)} given, but ${search}${grant}.`;
  }
  return [facts, state.reason, next].filter(Boolean).join(" ");
}

/** The known job websites ticked in the person's other searches: their permission stays. */
export function keptElsewhere(
  searches: readonly SavedSearch[],
  except: string,
): Set<Protected> {
  const kept = new Set<Protected>();
  for (const search of searches)
    if (search.id !== except)
      for (const platform of tickedOf(search.criteria.interactive_sites ?? []))
        kept.add(platform);
  return kept;
}
