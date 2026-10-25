import { describe, expect, it } from "vitest";

import { sourceLabel } from "@/features/jobs/labels";
import { FEED_KINDS } from "@/features/profile/api";
import { FEED_CATALOGUE } from "@/test/feeds";

import {
  FEED_NAMES,
  FEED_PROBLEM_UNKNOWN,
  FEED_PROBLEMS,
  FEED_SHORT_NAMES,
  feedProblem,
  feedSourceName,
  isFeedKind,
  isFeedReading,
  setAsideByRules,
  viaFeed,
} from "./source-words";

const DASHES = /[‒–—―]|\s-\s/;

// Every error code a feed reports (api/app/opportunities/sources/__init__.py ERRORS).
const API_CODES = [
  "not_found",
  "blocked",
  "rate_limited",
  "upstream_error",
  "timeout",
  "network",
  "too_large",
  "invalid_response",
  "too_many",
  "store_failed",
  "time_budget",
  "stub_missing",
];

describe("source words (O8)", () => {
  it("has plain words for every feed error code, none generic", () => {
    expect(Object.keys(FEED_PROBLEMS).sort()).toEqual([...API_CODES].sort());
    for (const code of API_CODES) {
      expect(feedProblem(code)).not.toBe(FEED_PROBLEM_UNKNOWN);
      expect(feedProblem(code)).not.toMatch(/_/);
    }
    expect(feedProblem("something_new")).toBe(FEED_PROBLEM_UNKNOWN);
  });

  it("uses no dash as a separator in any word", () => {
    for (const value of [
      ...Object.values(FEED_PROBLEMS),
      FEED_PROBLEM_UNKNOWN,
      ...Object.values(FEED_NAMES),
      ...Object.values(FEED_SHORT_NAMES),
      setAsideByRules(1),
      setAsideByRules(1200),
      ...FEED_CATALOGUE.not_supported.map((p) => p.reason),
    ])
      expect(value, value).not.toMatch(DASHES);
  });

  it("counts what the never apply rules set aside", () => {
    expect(setAsideByRules(0)).toBe("");
    expect(setAsideByRules(1)).toBe(
      "1 posting was set aside by your roles to never apply for.",
    );
    expect(setAsideByRules(1200)).toBe(
      "1,200 postings were set aside by your roles to never apply for.",
    );
  });

  it("knows every feed kind of the profile", () => {
    for (const kind of FEED_KINDS) {
      expect(isFeedKind(kind)).toBe(true);
      expect(FEED_NAMES[kind]).toBeTruthy();
    }
    expect(isFeedKind("google_careers")).toBe(false);
  });

  it("names a feed's source and says via which feed", () => {
    expect(feedSourceName("remoteok")).toBe("Remote OK");
    expect(feedSourceName("hackernews:freelance")).toBe(
      "Hacker News, freelance thread",
    );
    expect(feedSourceName("greenhouse:acme")).toBeNull();
    expect(viaFeed("remoteok")).toBe("via Remote OK");
    expect(viaFeed("weworkremotely, greenhouse:acme")).toBe(
      "via We Work Remotely",
    );
    expect(viaFeed("greenhouse:acme, remoteok")).toBeNull();
    expect(viaFeed(null)).toBeNull();
    expect(isFeedReading("Remote OK")).toBe(true);
    expect(isFeedReading("Hacker News, Freelancer? Seeking freelancer?")).toBe(
      true,
    );
    expect(isFeedReading("google_careers:ai")).toBe(false);
  });

  it("names feed sources on the posting page, alone or with others", () => {
    expect(sourceLabel("remoteok")).toBe("Remote OK");
    expect(sourceLabel("hackernews:hiring")).toBe("Hacker News, hiring thread");
    expect(sourceLabel("greenhouse:acme, remoteok")).toBe(
      "greenhouse:acme; Remote OK",
    );
    expect(sourceLabel("remoteok, weworkremotely")).toBe(
      "Remote OK; We Work Remotely",
    );
    expect(sourceLabel("google_careers:ai engineer, data/Paris")).toBe(
      "Google Careers, ai engineer, data, Paris",
    );
  });
});
