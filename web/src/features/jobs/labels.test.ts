import { describe, expect, it } from "vitest";

import {
  SOURCE_PROBLEMS,
  leftOutSentence,
  searchesSentence,
  sourceLabel,
  sourceName,
} from "./labels";

describe("sourceLabel", () => {
  it("names one browser source in words", () => {
    expect(sourceLabel("linkedin:AI Engineer/Paris")).toBe(
      "LinkedIn, AI Engineer, Paris",
    );
    expect(sourceLabel("greenhouse:example")).toBe("greenhouse:example");
  });

  it("names every source of a posting found twice (browser B10)", () => {
    expect(sourceLabel("greenhouse:example, linkedin:AI Engineer")).toBe(
      "greenhouse:example; LinkedIn, AI Engineer",
    );
    expect(sourceLabel("linkedin:AI Engineer, greenhouse:example")).toBe(
      "LinkedIn, AI Engineer; greenhouse:example",
    );
  });

  it("keeps a comma inside search words", () => {
    expect(sourceLabel("linkedin:AI Engineer, Agents")).toBe(
      "LinkedIn, AI Engineer, Agents",
    );
  });
});

describe("SOURCE_PROBLEMS", () => {
  it("says each LinkedIn refusal in words, without dashes", () => {
    for (const code of [
      "linkedin_off",
      "linkedin_paused",
      "linkedin_daily",
      "linkedin_signin",
      "linkedin_checkpoint",
    ]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/[–—]| - /);
    }
  });
});

describe("sourceName of a job website searched in the browser (jobs J15)", () => {
  it("names the four job websites and a website of the person's own by its host", () => {
    const named = (kind: string, host: string | null = null) =>
      sourceName({ kind, query: "", location: null, host });
    expect(named("linkedin")).toBe("LinkedIn");
    expect(named("welcome_to_the_jungle")).toBe("Welcome to the Jungle");
    expect(named("indeed")).toBe("Indeed");
    expect(named("freework")).toBe("Free-Work");
    expect(named("custom", "jobs.example-robotics.test")).toBe(
      "jobs.example-robotics.test",
    );
    expect(named("custom")).toBe("custom");
    // A reader with search words keeps them, as before.
    expect(sourceName({ kind: "linkedin", query: "AI Engineer" })).toBe(
      "LinkedIn, AI Engineer",
    );
  });

  it("says each outcome of a website search in words, without dashes", () => {
    for (const code of [
      "permission_unavailable",
      "permission_revoked",
      "permission_checkpoint",
      "platform_daily",
      "platform_signin",
      "finding_persistence_pending",
      "unsupported_interaction",
      "unsupported_criteria",
      "guard_failed",
      "no_job_list",
      "cookie_wall",
      "signin_wall",
      "no_search_box",
      "nothing_reported",
      "no_credential",
      "agent_failed",
      "page_budget",
      "token_budget",
    ]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/[–—]| - /);
    }
    // Said as signin_wall says it (review of PR 720).
    for (const code of ["no_job_list", "signin_wall"]) {
      expect(SOURCE_PROBLEMS[code]).toMatch(
        /Use Sign in beside it in Find jobs, then search again\.$/,
      );
    }
  });

  it("names no budget of a website's own agent (owner, 2026-10-27, jobs J18)", () => {
    // The agent of a website has no turn cap: the code is gone, and the page and token
    // sentences speak of a run, never of a day (no daily limit, owner 2026-10-28).
    expect(SOURCE_PROBLEMS.turn_limit).toBeUndefined();
    expect(SOURCE_PROBLEMS.page_budget).toBe(
      "The app read as many pages of this website as it may this time. What was read is kept.",
    );
    for (const code of ["page_budget", "token_budget"]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toContain("searches");
      expect(sentence).toMatch(/What was read is kept\.$/);
    }
  });
});

describe("no daily limit (owner, 2026-10-28)", () => {
  it("keeps the old daily codes readable without promising a search tomorrow", () => {
    for (const code of ["linkedin_daily", "platform_daily", "page_budget"]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/tomorrow|today/);
    }
    expect(SOURCE_PROBLEMS.linkedin_daily).toBe(
      "LinkedIn had already been searched that day, so it was not searched.",
    );
    expect(SOURCE_PROBLEMS.platform_daily).toBe(
      "This website had already been searched that day, so it was not searched.",
    );
  });
});

describe("searchesSentence (one agent per website, owner 2026-10-27)", () => {
  it("stays silent when the agent ran no search or the api is older", () => {
    expect(searchesSentence({})).toBe("");
    expect(searchesSentence({ searches: 0, searched: [] })).toBe("");
  });

  it("names each search the agent ran, a title in a place or a title alone", () => {
    expect(
      searchesSentence({
        searches: 1,
        searched: [{ title: "engineer", place: "Paris" }],
      }),
    ).toBe("1 search ran: engineer in Paris.");
    const sentence = searchesSentence({
      searches: 3,
      searched: [
        { title: "robot engineer", place: "Paris" },
        { title: "robot engineer", place: "Lyon" },
        { title: "drone pilot", place: null },
      ],
    });
    expect(sentence).toBe(
      "3 searches ran: robot engineer in Paris; robot engineer in Lyon; drone pilot.",
    );
    expect(sentence).not.toMatch(/[–—]| - /);
  });

  it("counts the searches it does not name, and says only the count from an older row", () => {
    const searched = Array.from({ length: 50 }, (_, n) => ({
      title: `t${n}`,
      place: null,
    }));
    expect(searchesSentence({ searches: 60, searched })).toMatch(
      /^60 searches ran: t0; t1; .*; t49, and 10 more\.$/,
    );
    expect(searchesSentence({ searches: 2 })).toBe("2 searches ran.");
    expect(searchesSentence({ searches: 1_234, searched: [] })).toBe(
      "1,234 searches ran.",
    );
  });
});

describe("leftOutSentence (more saved values than a website's search takes)", () => {
  it("stays silent when nothing was left out or the api is older", () => {
    expect(leftOutSentence({})).toBe("");
    expect(leftOutSentence({ titles_left_out: 0, places_left_out: 0 })).toBe(
      "",
    );
  });

  it("says how many saved titles and places were left out", () => {
    expect(leftOutSentence({ titles_left_out: 1 })).toBe(
      "1 saved title was left out: a website's search types plain words, the first ones that fit.",
    );
    expect(leftOutSentence({ places_left_out: 3 })).toBe(
      "3 saved places were left out: a website's search types plain words, the first ones that fit.",
    );
    expect(leftOutSentence({ titles_left_out: 1, places_left_out: 1 })).toBe(
      "1 saved title and 1 saved place were left out: a website's search types plain words, the first ones that fit.",
    );
    const sentence = leftOutSentence({
      titles_left_out: 1_200,
      places_left_out: 20,
    });
    expect(sentence).toBe(
      "1,200 saved titles and 20 saved places were left out: a website's search types plain words, the first ones that fit.",
    );
    expect(sentence).not.toMatch(/[–—]| - /);
  });
});
