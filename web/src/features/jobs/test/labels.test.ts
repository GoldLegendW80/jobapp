import { describe, expect, it } from "vitest";

import {
  SOURCE_PROBLEMS,
  guestSentence,
  leftOutSentence,
  searchesSentence,
  sourceLabel,
  sourceName,
  tabClosedSentence,
} from "../helpers/labels";

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

  it("names Anywhere on the web, alone and beside another source (jobs J21)", () => {
    expect(sourceLabel("web")).toBe("Anywhere on the web");
    expect(sourceLabel("linkedin:AI Engineer, web")).toBe(
      "LinkedIn, AI Engineer; Anywhere on the web",
    );
    expect(sourceLabel("web, linkedin:AI Engineer")).toBe(
      "Anywhere on the web; LinkedIn, AI Engineer",
    );
    expect(
      sourceName({ kind: "web", query: "", location: null, host: "web" }),
    ).toBe("Anywhere on the web");
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
      "browser_memory",
      "no_job_list",
      "cookie_wall",
      "signin_wall",
      "no_search_box",
      "search_refused",
      "security_check",
      "nothing_reported",
      "web_search_failed",
      "plan_unfinished",
      "no_credential",
      "agent_failed",
      "agent_cancelled",
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

  it("says how many of the planned searches ran when a cap stopped LinkedIn's public job search", () => {
    expect(searchesSentence({ searches: 11, planned: 21 })).toBe(
      "11 of 21 searches ran.",
    );
    expect(
      searchesSentence({
        searches: 1,
        planned: 2,
        searched: [{ title: "engineer", place: "Paris" }],
      }),
    ).toBe("1 of 2 searches ran: engineer in Paris.");
    // All planned ran, or an older row without the count: the count alone.
    expect(searchesSentence({ searches: 21, planned: 21 })).toBe(
      "21 searches ran.",
    );
    expect(searchesSentence({ searches: 3, planned: 0 })).toBe(
      "3 searches ran.",
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

describe("guestSentence", () => {
  it("says when LinkedIn's public job search stood in for the browser search", () => {
    expect(
      guestSentence({ fallback: "guest", fallback_after: "linkedin_signin" }),
    ).toBe(
      "Searched with LinkedIn's public job search, as you are not signed in to LinkedIn.",
    );
    expect(
      guestSentence({ fallback: "guest", fallback_after: "search_refused" }),
    ).toBe(
      "Searched with LinkedIn's public job search, as the browser search failed.",
    );
  });

  it("says when LinkedIn's public page in the browser stood in for the public API (jobs J22)", () => {
    expect(
      guestSentence({
        fallback: "page",
        fallback_after: "linkedin_rate_limited",
        not_searched: [{ title: "AI Engineer", place: "Reunion" }],
      }),
    ).toBe(
      "Searched with LinkedIn's public job search page, as the public API refused. Not searched this time: AI Engineer in Reunion.",
    );
    expect(
      guestSentence({ fallback: "page", fallback_after: "layout_changed" }),
    ).toBe(
      "Searched with LinkedIn's public job search page, as the public API failed.",
    );
    expect(
      guestSentence({ fallback: "page", fallback_after: "page_failed" }),
    ).toBe(
      "Searched with LinkedIn's public job search page, as the public API failed.",
    );
    expect(SOURCE_PROBLEMS.linkedin_page_refused).toContain(
      "public job search page",
    );
  });

  it("counts the postings of other places and names the pairs not searched", () => {
    expect(
      guestSentence({
        other_place: 7,
        not_searched: [
          { title: "AI Engineer", place: "Lyon" },
          { title: "ML Engineer", place: null },
        ],
      }),
    ).toBe(
      "7 postings of a place you did not choose left out. Not searched this time: AI Engineer in Lyon; ML Engineer.",
    );
    expect(guestSentence({ other_place: 1 })).toBe(
      "1 posting of a place you did not choose left out.",
    );
  });

  it("says nothing for an older api or another source", () => {
    expect(guestSentence({})).toBe("");
    expect(guestSentence({ fallback: null, not_searched: [] })).toBe("");
  });
});

// browser B29: a website's tab closed and opened again, by the reason the app knew.
describe("tabClosedSentence", () => {
  it("says each close by its reason, the MiB in use and the media left out, in one sentence", () => {
    const said = tabClosedSentence({
      tab_closed: 3,
      tab_closed_reasons: { memory: 2, other: 1 },
      tab_closed_mib: [1850, 1902],
      media_blocked: true,
    });
    expect(said).toBe(
      "The website's tab was closed and opened again 3 times (twice as the browser's memory ran short, at 1,850 MiB and 1,902 MiB; once for a reason the app did not see), and the search then loaded no images, videos or fonts.",
    );
    expect(said).not.toMatch(/[–—]| - /);
    expect(said.split(". ")).toHaveLength(1);
  });

  it("names a crash, and no cause the app did not see", () => {
    expect(
      tabClosedSentence({ tab_closed: 1, tab_closed_reasons: { crash: 1 } }),
    ).toBe(
      "The website's tab was closed and opened again once as the page crashed.",
    );
    expect(
      tabClosedSentence({
        tab_closed: 1,
        tab_closed_reasons: { memory: 1 },
        tab_closed_mib: [1850],
        media_blocked: true,
      }),
    ).toBe(
      "The website's tab was closed and opened again once as the browser's memory ran short, at 1,850 MiB, and the search then loaded no images, videos or fonts.",
    );
    expect(
      tabClosedSentence({ tab_closed: 1, tab_closed_reasons: { other: 1 } }),
    ).not.toMatch(/memory|crash/);
  });

  it("reads a reason the app does not know as none, a key of every object included", () => {
    for (const reason of [
      "constructor",
      "toString",
      "__proto__",
      "hasOwnProperty",
    ]) {
      const said = tabClosedSentence({
        tab_closed: 1,
        tab_closed_reasons: JSON.parse(`{"${reason}": 1}`),
      });
      expect(said).toBe("The website's tab was closed and opened again once.");
    }
  });

  it("says nothing for an older api or a tab never closed", () => {
    expect(tabClosedSentence({})).toBe("");
    expect(tabClosedSentence({ tab_closed: 0, media_blocked: false })).toBe("");
  });
});
