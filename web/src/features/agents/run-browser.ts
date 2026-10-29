/**
 * Which part of the browser a website search run's page shows (browser B18): pure, so the cases
 * of its lease are a table in run-live-view.test.tsx.
 */
import type { Browser, Lease } from "@/features/browser/browser-api";

import type { Run } from "./runs-api";

/** The agent of a job website's search (api finder.INTERACTIVE_NAME), one run a website. */
export const WEBSITE_AGENT = "browser_discovery";
/** The purpose of the lease a sweep holds while it searches a website (api leases.LEASE_MAX_S). */
const SWEEP_PURPOSE = "sweep";
export const STARTING = "Your browser is starting.";
export const ELSEWHERE =
  "The browser shows here while this website is searched.";

export type RunBrowser =
  { show: "starting" } | { show: "elsewhere" } | { show: "live"; lease: Lease };

/**
 * Whether this lease is the run's: an assistant's sweep lease (never the person's own session)
 * that started at or before the run. The search takes its lease first and one lease is held at a
 * time, so a later one is another website's. Fails closed: a time that cannot be read is not
 * the run's. browserd's start against the api's run time: right only while both share the host
 * clock (as apply's live view slot).
 */
function isThisRuns(run: Run, lease: Lease): boolean {
  if (!lease.automation || lease.purpose !== SWEEP_PURPOSE) return false;
  return Date.parse(lease.started_at) <= Date.parse(run.started_at ?? "");
}

/**
 * What the page of a running website search shows of the browser. No lease yet, or the lease
 * still starting: the sentence that says so. The lease of another purpose, another person or
 * another website: the line that says where the browser shows. Else the live view.
 */
export function browserOfRun(
  run: Run,
  browser: Browser | undefined,
): RunBrowser {
  if (!browser) return { show: "starting" };
  if (browser.holder === "other") return { show: "elsewhere" };
  const lease = browser.holder === "you" ? browser.lease : null;
  if (!lease) return { show: "starting" };
  if (!isThisRuns(run, lease)) return { show: "elsewhere" };
  return lease.state === "starting"
    ? { show: "starting" }
    : { show: "live", lease };
}
