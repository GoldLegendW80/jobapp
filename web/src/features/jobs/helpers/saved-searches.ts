/** The words of Your searches (find jobs, jobs J33): what a search looks for, when it runs, its
 * last run, and the websites the other searches keep a permission for. */
import { describeSchedule } from "@/features/profile/helpers/schedule";

import type { Criteria, SavedSearch } from "../api/searches-api";
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

/** The newest sweep of a search among the last ones listed. */
export function lastRunOf(
  id: string,
  sweeps: readonly Sweep[],
): Sweep | undefined {
  return sweeps.find((sweep) => searchOf(sweep) === id);
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
