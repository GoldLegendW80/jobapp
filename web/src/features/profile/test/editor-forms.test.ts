import { describe, expect, it } from "vitest";

import { haraldLike } from "@/test/profile";

import { EMPTY } from "../api/profile-api";
import {
  fromSearchForm,
  fromSkillsForm,
  inWords,
  searchSchema,
  skillsSchema,
  toSearchForm,
  toSkillsForm,
  toWordsForm,
  withWords,
} from "../helpers/editor-forms";

const issues = (result: {
  success: boolean;
  error?: { issues: { path: PropertyKey[]; message: string }[] };
}) => (result.error?.issues ?? []).map((i) => [i.path.join("."), i.message]);

describe("round trips", () => {
  it("gives back exactly what the api stored, for each editor", () => {
    const data = haraldLike();
    expect(fromSkillsForm(toSkillsForm(data.skills))).toEqual(data.skills);
    expect(fromSearchForm(toSearchForm(data.search))).toEqual(data.search);
  });

  // Owner, 2026-10-28: boards and feeds are gone; a section read before carries none on.
  it("sends no boards nor feeds a section stored before the deletion held", () => {
    const data = haraldLike();
    const older = {
      ...data.search,
      boards: [{ company: "Example Labs", ats: "ashby", org: "example-labs" }],
      feeds: [{ kind: "remoteok", terms: [], enabled: true }],
    };
    const sent = fromSearchForm(toSearchForm(older));
    expect(sent).not.toHaveProperty("boards");
    expect(sent).not.toHaveProperty("feeds");
    expect(sent).toEqual(data.search);
  });

  it("handles empty sections", () => {
    expect(fromSkillsForm(toSkillsForm(EMPTY.skills))).toEqual(EMPTY.skills);
    expect(fromSearchForm(toSearchForm(EMPTY.search))).toEqual(EMPTY.search);
    expect(toSkillsForm(EMPTY.skills).years_experience).toBe("");
    expect(toSearchForm(EMPTY.search).remote_stance).toBe("");
  });

  it("cleans what it sends: trims, drops duplicate terms", () => {
    const search = toSearchForm(haraldLike().search);
    search.location.terms = [" Paris ", "paris", "Lyon", ""];
    search.contract_types = ["VIE", "CDI"];
    search.days = " 7 ";
    search.never_apply = [{ value: "  No defence. " }];
    const sent = fromSearchForm(search);
    expect(sent.location.terms).toEqual(["Paris", "Lyon"]);
    expect(sent.contract_types).toEqual(["CDI", "VIE"]);
    expect(sent.days).toBe(7);
    expect(sent.never_apply).toEqual(["No defence."]);
  });

  it("reads years as a number, empty as null", () => {
    const form = toSkillsForm(haraldLike().skills);
    expect(
      fromSkillsForm({ ...form, years_experience: "0" }).years_experience,
    ).toBe(0);
    expect(
      fromSkillsForm({ ...form, years_experience: "12.25" }).years_experience,
    ).toBe(12.25);
    expect(
      fromSkillsForm({ ...form, years_experience: "  " }).years_experience,
    ).toBeNull();
  });
});

describe("skills in words beside the stored expressions", () => {
  const skills = () => {
    const data = haraldLike().skills;
    return {
      ...data,
      none: [
        ...data.none,
        { name: "java", advanced_regex: true, terms: ["\\bjava\\b"] },
      ],
    };
  };

  it("gives the fields of About you the years and the skills in words, and saves them after the expressions", () => {
    const stored = skills();
    const form = toWordsForm(stored);
    expect(form.years_experience).toBe("3.5");
    expect(form.have.map((one) => one.name)).toEqual(["agents"]);
    expect(form.none.map((one) => one.name)).toEqual(["rust"]);
    const edited = fromSkillsForm(form);
    edited.years_experience = 4;
    edited.adjacent = [];
    const sent = withWords(stored, edited);
    expect(sent.years_experience).toBe(4);
    expect(sent.have.map((one) => one.name)).toEqual(["llm", "agents"]);
    expect(sent.adjacent).toEqual([]);
    expect(sent.none.map((one) => one.name)).toEqual(["java", "rust"]);
    expect(sent.gates).toEqual(stored.gates);
  });

  it("names the skills in words alone", () => {
    const words = inWords(skills());
    expect(words.have.map((one) => one.name)).toEqual(["agents"]);
    expect(words.none.map((one) => one.name)).toEqual(["rust"]);
    expect(words.years_experience).toBe(3.5);
  });
});

describe("skills schema", () => {
  const base = () => toSkillsForm(haraldLike().skills);

  it.each(["0", "3.5", "60", "12.25", ""])("accepts %j years", (years) => {
    expect(
      skillsSchema.safeParse({ ...base(), years_experience: years }).success,
    ).toBe(true);
  });

  it.each(["60.5", "-1", "abc", "1e2", "3,5", "100"])(
    "refuses %j years",
    (years) => {
      expect(
        issues(skillsSchema.safeParse({ ...base(), years_experience: years })),
      ).toEqual([
        [
          "years_experience",
          "Enter a number of years between 0 and 60, such as 3.5.",
        ],
      ]);
    },
  );

  it("marks a term whose group name an earlier term of the list uses", () => {
    const form = base();
    form.have[1]!.terms = ["(?P<n>llm)", "(?P<n>genai)"];
    expect(issues(skillsSchema.safeParse(form))).toEqual([
      [
        "have.1.terms.1",
        'The group name "n" is already used by term 1: group names must be unique across the list.',
      ],
    ]);
  });

  it("refuses a category name used in another list, ignoring case", () => {
    const form = base();
    form.none.push({ name: "AGENTS", advanced_regex: false, terms: [] });
    expect(issues(skillsSchema.safeParse(form))).toEqual([
      ["none.1.name", "Another category already has this name."],
    ]);
  });

  it("puts a bad term at its own index", () => {
    const form = base();
    form.have[1]!.terms = ["ok", "(a+)+", "(bad"];
    const found = issues(skillsSchema.safeParse(form));
    expect(found.map(([path]) => path)).toEqual([
      "have.1.terms.1",
      "have.1.terms.2",
    ]);
    expect(found[0]![1]).toMatch(/^Nested quantifiers/);
    expect(found[1]![1]).toMatch(/^Invalid regular expression/);
  });

  it("refuses a penalty outside 0 to 100 and too many terms", () => {
    const form = base();
    form.gates[0]!.penalty = 101;
    form.have[0]!.terms = Array.from({ length: 301 }, (_, i) => `t${i}`);
    expect(issues(skillsSchema.safeParse(form)).map(([p]) => p)).toEqual([
      "have.0.terms",
      "gates.0.penalty",
    ]);
  });
});

describe("search schema", () => {
  const base = () => toSearchForm(haraldLike().search);

  it("refuses an over long rule and days out of range", () => {
    const form = base();
    form.never_apply = [{ value: "x".repeat(501) }];
    form.days = "0";
    expect(issues(searchSchema.safeParse(form)).map(([p]) => p)).toEqual([
      "days",
      "never_apply.0.value",
    ]);
  });
});

describe("browser sources (browser B8)", () => {
  it.each([
    { enabled: false, location: null },
    { enabled: true, location: "Nantes" },
  ])("validates and round trips Free-Work settings: %j", (settings) => {
    const source = {
      kind: "freework" as const,
      query: "Robotics Engineer",
      ...settings,
    };
    const search = { ...haraldLike().search, browser_sources: [source] };
    const parsed = searchSchema.safeParse(toSearchForm(search));
    expect(parsed.success).toBe(true);
    if (!parsed.success) throw parsed.error;
    expect(fromSearchForm(parsed.data).browser_sources).toEqual([source]);
  });

  const withSources = () => {
    const profile = haraldLike();
    return {
      ...profile.search,
      browser_sources: [
        {
          kind: "linkedin" as const,
          query: "ai engineer",
          location: "Paris",
          enabled: true,
        },
        {
          kind: "freework" as const,
          query: "data",
          location: null,
          enabled: false,
        },
      ],
    };
  };

  it("keeps every source through a save of the Search tab", () => {
    const search = withSources();
    expect(fromSearchForm(toSearchForm(search)).browser_sources).toEqual(
      search.browser_sources,
    );
  });

  // Owner, 2026-10-28: the fixed readers are gone; only LinkedIn and Free-Work rows stay.
  it("sends no row of a fixed reader the profile still held", () => {
    const search = withSources();
    const older = {
      ...search,
      browser_sources: [
        { kind: "google_careers", query: "ai", location: null, enabled: true },
        ...search.browser_sources,
        { kind: "vie", query: "data", location: null, enabled: false },
      ],
    } as unknown as typeof search;
    expect(fromSearchForm(toSearchForm(older)).browser_sources).toEqual(
      search.browser_sources,
    );
  });

  it("leaves the field out when there is none, as before B8", () => {
    const search = { ...withSources(), browser_sources: [] };
    expect(fromSearchForm(toSearchForm(search))).not.toHaveProperty(
      "browser_sources",
    );
    const older: Partial<typeof search> = { ...search };
    delete older.browser_sources;
    expect(
      fromSearchForm(toSearchForm(older as typeof search)),
    ).not.toHaveProperty("browser_sources");
  });

  it("trims the words, sends an empty location as null", () => {
    const form = toSearchForm(withSources());
    form.browser_sources[0] = {
      ...form.browser_sources[0]!,
      query: "  ai engineer ",
      location: "   ",
    };
    expect(fromSearchForm(form).browser_sources?.[0]).toEqual({
      kind: "linkedin",
      query: "ai engineer",
      location: null,
      enabled: true,
    });
  });

  it("asks for the words and keeps at most 10 sources", () => {
    const form = toSearchForm(withSources());
    form.browser_sources[1] = { ...form.browser_sources[1]!, query: " " };
    expect(issues(searchSchema.safeParse(form))).toEqual([
      ["browser_sources.1.query", "Enter the search words."],
    ]);
    const full = toSearchForm(withSources());
    full.browser_sources = Array.from({ length: 11 }, (_, n) => ({
      kind: "freework" as const,
      query: `words ${n}`,
      location: "",
      enabled: true,
    }));
    expect(issues(searchSchema.safeParse(full))).toEqual([
      ["browser_sources", "Keep at most 10 sources."],
    ]);
  });
});
