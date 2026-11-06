import { describe, expect, it } from "vitest";

import {
  EXAMPLES,
  addressProblem,
  checkSite,
  draftOfSearch,
  feedOf,
  listed,
  missingRecommended,
  namesSentence,
  RECOMMENDED,
  resumed,
  rowsOf,
  siteOf,
  sourceCount,
  withBoard,
  withFeed,
  withScheme,
  withoutRow,
  withRecommended,
  type SourcesDraft,
} from "../helpers/sources";
import { EMPTY } from "@/features/profile/api/profile-api";

const EMPTY_DRAFT: SourcesDraft = { sites: [], boards: null, feeds: [] };

describe("an address the person types", () => {
  it("gets https when typed without its scheme, and keeps one typed", () => {
    expect(withScheme("  jobs.example.org/careers ")).toBe(
      "https://jobs.example.org/careers",
    );
    expect(withScheme("http://jobs.example.org")).toBe(
      "http://jobs.example.org",
    );
    expect(withScheme("   ")).toBe("");
  });

  it.each([
    ["", "Paste or type an address first."],
    ["jobs example org", "An address has no spaces."],
    ["localhost", "This is not a website address."],
    ["ftp://jobs.example.org", "This is not a website address."],
  ])("refuses %j before any api call", (text, sentence) => {
    expect(addressProblem(text)).toContain(sentence);
  });

  it("accepts the shape of a public address", () => {
    expect(addressProblem("jobs.example.org/careers")).toBeNull();
    expect(addressProblem("https://www.example.com/jobs")).toBeNull();
  });

  it("names the job feed of a feed's own website, any Adzuna country included", () => {
    expect(feedOf("https://www.arbeitnow.com/jobs")).toBe("arbeitnow");
    expect(feedOf("https://himalayas.app/")).toBe("himalayas");
    expect(feedOf("https://www.adzuna.fr/")).toBe("adzuna");
    expect(feedOf("https://www.adzuna.co.uk/jobs")).toBe("adzuna");
    expect(feedOf("https://careers.example.org/")).toBeNull();
  });

  it("is a website of her own unless an example names its platform", () => {
    expect(siteOf("https://fr.indeed.com/").platform).toBe("custom");
    expect(siteOf("https://www.indeed.com/", "indeed").platform).toBe("indeed");
  });
});

describe("the list", () => {
  it("refuses an address already there and an eleventh website", () => {
    const first = checkSite([], siteOf("https://jobs.example.org/"));
    if ("error" in first) throw new Error(first.error);
    expect(
      checkSite(first.sites, siteOf("https://JOBS.example.org/#x")),
    ).toEqual({ error: "This website is already in your list." });
    const ten = Array.from({ length: 10 }, (_, at) =>
      siteOf(`https://site${String(at)}.example.org/`),
    );
    expect(checkSite(ten, siteOf("https://one-more.example.org/"))).toEqual({
      error: "Keep at most 10 websites. Remove one to add another.",
    });
  });

  it("keeps the feeds in their own order, each once", () => {
    expect(withFeed(withFeed([], "adzuna"), "arbeitnow")).toEqual([
      "arbeitnow",
      "adzuna",
    ]);
    expect(withFeed(["arbeitnow"], "arbeitnow")).toEqual(["arbeitnow"]);
  });

  it("refuses a board already there, case aside", () => {
    const board = {
      company: "Example Labs",
      ats: "greenhouse" as const,
      board: "examplelabs",
      enabled: true,
    };
    expect(withBoard([board], { ...board, board: "ExampleLabs" })).toEqual({
      error: "Example Labs is already in your list.",
    });
  });

  it("shows every store as lines, says how each is read, and counts the places searched", () => {
    const draft: SourcesDraft = {
      sites: [
        siteOf("https://www.linkedin.com/", "linkedin"),
        { ...siteOf("https://careers.example.org/"), enabled: false },
      ],
      feeds: ["himalayas"],
      boards: [
        {
          company: "Example Labs",
          ats: "ashby",
          board: "examplelabs",
          enabled: true,
        },
      ],
    };
    const rows = rowsOf(draft);
    expect(rows.map((row) => [row.name, row.how, row.enabled])).toEqual([
      ["LinkedIn", "Job website, searched by the assistant", true],
      [
        "careers.example.org",
        "Website of your choice, browsed by the assistant",
        false,
      ],
      [
        "Himalayas",
        "Remote jobs open to France, read through its official feed",
        true,
      ],
      ["Example Labs", "Company job board on Ashby, read directly", true],
    ]);
    expect(rows[0]?.platform).toBe("linkedin");
    // The paused website is not searched.
    expect(sourceCount(draft)).toBe(3);
    const second = rows[1];
    if (!second) throw new Error("no second line");
    expect(sourceCount(resumed(draft, second))).toBe(4);
    expect(rowsOf(withoutRow(draft, second)).map((row) => row.name)).toEqual([
      "LinkedIn",
      "Himalayas",
      "Example Labs",
    ]);
    expect(listed(draft, "https://himalayas.app/")).toBe(true);
    expect(listed(draft, "https://www.linkedin.com/#top")).toBe(true);
  });

  it("reads a search section that never saved a board list as no board at all", () => {
    expect(draftOfSearch(structuredClone(EMPTY.search))).toEqual(EMPTY_DRAFT);
  });

  it("offers examples of every kind, with no dash as a separator", () => {
    expect(EXAMPLES.map((example) => example.label)).toEqual([
      "LinkedIn",
      "Welcome to the Jungle",
      "Indeed",
      "Free-Work",
      "Arbeitnow",
      "Himalayas",
      "Doctolib on Ashby",
      "Datadog on Greenhouse",
    ]);
    for (const example of EXAMPLES)
      expect(example.label).not.toMatch(/[–—]| - /);
  });
});

describe("the recommended places", () => {
  it("are the four job websites and the two feeds, never a company board", () => {
    expect(RECOMMENDED.map((example) => example.label)).toEqual([
      "LinkedIn",
      "Welcome to the Jungle",
      "Indeed",
      "Free-Work",
      "Arbeitnow",
      "Himalayas",
    ]);
  });

  it("adds what the draft lacks once, a known website under any address counting as listed", () => {
    const draft: SourcesDraft = {
      sites: [siteOf("https://fr.linkedin.com/jobs/")],
      boards: null,
      feeds: ["arbeitnow"],
    };
    expect(missingRecommended(draft).map((example) => example.label)).toEqual([
      "Welcome to the Jungle",
      "Indeed",
      "Free-Work",
      "Himalayas",
    ]);
    const result = withRecommended(draft);
    expect(result.left).toEqual([]);
    expect(result.draft.sites.map((site) => site.platform)).toEqual([
      "custom",
      "welcome_to_the_jungle",
      "indeed",
      "freework",
    ]);
    expect(result.draft.feeds).toEqual(["arbeitnow", "himalayas"]);
    expect(missingRecommended(result.draft)).toEqual([]);
    expect(withRecommended(result.draft).added).toEqual([]);
  });

  it("stops the websites at ten and says which were left", () => {
    const sites = Array.from({ length: 9 }, (_, index) =>
      siteOf(`https://careers${String(index)}.example.org/`),
    );
    const result = withRecommended({ sites, boards: null, feeds: [] });
    expect(result.draft.sites).toHaveLength(10);
    expect(result.added).toEqual(["LinkedIn", "Arbeitnow", "Himalayas"]);
    expect(result.left).toEqual([
      "Welcome to the Jungle",
      "Indeed",
      "Free-Work",
    ]);
    expect(namesSentence(result.left)).toBe(
      "Welcome to the Jungle, Indeed and Free-Work",
    );
  });
});
