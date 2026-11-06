/**
 * The job feeds a search reads beside the job websites (api app/jobs/feeds, owner 2026-10-30):
 * Arbeitnow, Himalayas and Adzuna, official job APIs. Their names and labels, what each code of
 * a feed's entry in a sweep's `sources` means, and where Adzuna's key is stored (its requests:
 * api/job-feeds-api.ts).
 */
import type { FeedState, FeedsState } from "../api/job-feeds-api";

export const JOB_FEEDS = ["arbeitnow", "himalayas", "adzuna"] as const;
export type JobFeed = (typeof JOB_FEEDS)[number];

export const isJobFeed = (kind: string): kind is JobFeed =>
  (JOB_FEEDS as readonly string[]).includes(kind);

/** The short name of a feed, as a posting's source and a sweep's summary name it. */
export const FEED_NAMES: Record<JobFeed, string> = {
  arbeitnow: "Arbeitnow",
  himalayas: "Himalayas",
  adzuna: "Adzuna",
};

export const ADZUNA_SIGNUP = "https://developer.adzuna.com/signup";
/** Where the person stores Adzuna's key: a card of the Account page. */
export const ADZUNA_KEY_PLACE = "/account#adzuna-key";

/** What a feed's `error` in a sweep's summary means. */
export const FEED_WORDS: Record<string, string> = {
  rate_limited:
    "The site asked the app to slow down, so it is read again at the next search. What was read is kept.",
  blocked: "The site refused to answer the app, so nothing was read.",
  not_found: "The feed was not found at its address, so nothing was read.",
  upstream_error:
    "The site had a problem on its side, so it is read again at the next search.",
  timeout:
    "The site took too long to answer, so it is read again at the next search.",
  network:
    "The site could not be reached, so it is read again at the next search.",
  too_large: "The feed's answer was too large to read.",
  invalid_response: "The feed did not answer as expected.",
  store_failed: "What this feed found could not be saved.",
  time_budget:
    "The search ran out of time before this feed was read to the end. What was read is kept.",
  no_key:
    "Skipped: no Adzuna key is stored. Add your free key in Account to search Adzuna.",
  key_unreadable:
    "Skipped: the stored Adzuna key cannot be read by this server. Save it again in Account.",
  key_refused:
    "Adzuna refused the stored key. Check it at developer.adzuna.com and save it again in Account.",
  unsupported_criteria:
    "Not searched: add a job title you want, so the feed has words to search.",
  feed_failed: "The feed could not be read this time.",
  page_cap:
    "The feed had more new postings than the app reads in one search. What was read is kept, and the next search reads back to the same point.",
};

export const feedWords = (code: string): string =>
  FEED_WORDS[code] ?? "The feed could not be read this time.";

/** Adzuna's state in the listing, undefined while it loads or when it failed. */
export const adzunaOf = (
  state: FeedsState | undefined,
): FeedState | undefined => state?.feeds.find((feed) => feed.name === "adzuna");
