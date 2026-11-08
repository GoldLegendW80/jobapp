import { describe, expect, it } from "vitest";

import {
  EMPTY,
  type Answers,
  type Search,
} from "@/features/profile/api/profile-api";

import {
  answersWith,
  draftOf,
  nextPage,
  openStep,
  passedOf,
  readPay,
  rangesOf,
  resumeStep,
  searchWith,
  splitTerms,
  stepProblem,
  UNSTORED,
  type Draft,
} from "../helpers/steps";

const draft = (patch: Partial<Draft> = {}): Draft => ({
  ...draftOf(EMPTY),
  ...patch,
});

describe("splitTerms", () => {
  it.each([
    ["", []],
    ["   ", []],
    [",,, ,", []],
    ["Data engineer", ["Data engineer"]],
    [
      "Data engineer, Analytics engineer",
      ["Data engineer", "Analytics engineer"],
    ],
    ["a,b\nc", ["a", "b", "c"]],
    ["  spaced   out  ,x", ["spaced out", "x"]],
    ["Data engineer, data ENGINEER", ["Data engineer"]],
    ["Ingénieur données, Ingénieur Données", ["Ingénieur données"]],
    ["C++, C#", ["C++", "C#"]],
  ])("%j", (text, terms) => {
    expect(splitTerms(text)).toEqual(terms);
  });
});

describe("readPay", () => {
  const pay = (min: string, max: string, currency = "EUR") =>
    readPay({ min, max, currency });
  it.each([
    [pay("", ""), { range: null }],
    [pay(" ", " "), { range: null }],
    [
      pay("40000", "50000"),
      { range: { min: 40000, max: 50000, currency: "EUR" } },
    ],
    [pay("0", "0"), { range: { min: 0, max: 0, currency: "EUR" } }],
    [
      pay("500", "500", "CHF"),
      { range: { min: 500, max: 500, currency: "CHF" } },
    ],
    [
      pay("10000000", "10000000"),
      { range: { min: 10_000_000, max: 10_000_000, currency: "EUR" } },
    ],
  ])("accepts %j", (read, expected) => {
    expect(read).toEqual(expected);
  });
  it.each([
    ["40000", ""],
    ["", "50000"],
    ["50000", "40000"],
    ["-1", "10"],
    ["1.5", "10"],
    ["40 000", "50000"],
    ["40k", "50k"],
    ["1e5", "2e5"],
    ["10000001", "10000002"],
    ["123456789", "123456789"],
  ])("refuses %j to %j", (min, max) => {
    expect("error" in pay(min, max)).toBe(true);
  });
  it("refuses an unknown currency", () => {
    expect("error" in pay("1", "2", "XXX")).toBe(true);
  });
});

describe("stepProblem", () => {
  it("asks for at least one role", () => {
    expect(stepProblem("looking", draft())).toMatch(/at least one role/);
    expect(stepProblem("looking", draft({ roles: " , " }))).toMatch(/role/);
    expect(
      stepProblem("looking", draft({ roles: "Data engineer" })),
    ).toBeNull();
  });
  it("asks for a place or Anywhere, and a stance", () => {
    expect(stepProblem("where", draft({ remote: "hybrid" }))).toMatch(/place/);
    expect(stepProblem("where", draft({ locations: "Lyon" }))).toMatch(
      /remote/,
    );
    expect(
      stepProblem("where", draft({ anywhere: true, remote: "any" })),
    ).toBeNull();
    expect(
      stepProblem("where", draft({ locations: "Lyon", remote: "office" })),
    ).toBeNull();
  });
  it("checks the pay unless Rather not say", () => {
    const bad = { min: "9", max: "1", currency: "EUR" };
    expect(stepProblem("pay", draft({ salary: bad }))).toMatch(/salary/);
    expect(stepProblem("pay", draft({ daily: bad }))).toMatch(/daily rate/);
    expect(
      stepProblem("pay", draft({ salary: bad, ratherNotSay: true })),
    ).toBeNull();
    expect(stepProblem("pay", draft())).toBeNull();
  });
  it("never blocks the steps that are optional", () => {
    for (const step of ["profile", "work", "apply", "summary"] as const)
      expect(stepProblem(step, draft())).toBeNull();
  });
  it("asks for the country of a work authorisation, and whether the person may work there", () => {
    const authorization = {
      country: "",
      allowed: null,
      sponsorship: null,
      kept: "",
    };
    expect(
      stepProblem(
        "work",
        draft({ authorization: { ...authorization, allowed: "yes" } }),
      ),
    ).toBe(
      "Name the country or region where you may work, for example France.",
    );
    expect(
      stepProblem(
        "work",
        draft({ authorization: { ...authorization, country: " France " } }),
      ),
    ).toBe("Say whether you may work in France.");
    // A stored answer that says more than yes or no is kept as it is.
    expect(
      stepProblem(
        "work",
        draft({
          authorization: {
            ...authorization,
            country: "France",
            kept: "EU citizen",
          },
        }),
      ),
    ).toBeNull();
  });
});

describe("searchWith", () => {
  const stored: Search = {
    ...EMPTY.search,
    min_fit: 55,
    never_apply: ["No defence roles."],
  };
  it("writes this step's answers and keeps everything else", () => {
    const saved = searchWith(
      "looking",
      stored,
      draft({ roles: "Data engineer, ML engineer", seniority: ["senior"] }),
    );
    expect(saved.title_include).toEqual({
      terms: ["Data engineer", "ML engineer"],
      advanced_regex: false,
    });
    expect(saved.seniority).toEqual(["senior"]);
    expect(saved.min_fit).toBe(55);
    expect(saved.never_apply).toEqual(stored.never_apply);
  });
  it("never rewrites a list kept as patterns", () => {
    const patterns = { terms: ["^data (eng|sci)"], advanced_regex: true };
    const saved = searchWith(
      "looking",
      { ...stored, title_include: patterns },
      draft({ roles: "something else" }),
    );
    expect(saved.title_include).toEqual(patterns);
  });
  it("Anywhere clears the places", () => {
    const saved = searchWith(
      "where",
      { ...stored, location: { terms: ["Lyon"], advanced_regex: false } },
      draft({ anywhere: true, locations: "Lyon", remote: "remote" }),
    );
    expect(saved.location.terms).toEqual([]);
    expect(saved.remote_stance).toBe("remote");
  });
  it("Rather not say clears both figures", () => {
    const saved = searchWith(
      "pay",
      {
        ...stored,
        compensation: {
          salary: { min: 1, max: 2, currency: "EUR" },
          daily_rate: null,
        },
      },
      draft({ ratherNotSay: true }),
    );
    expect(saved.compensation).toEqual({ salary: null, daily_rate: null });
  });
  it("leaves the search alone on the other steps", () => {
    expect(searchWith("apply", stored, draft({ roles: "x" }))).toBe(stored);
  });
});

describe("progress", () => {
  it("resumes at the first step neither done nor passed", () => {
    expect(resumeStep([], [])).toBe("access");
    // Model access is done once a credential is saved.
    expect(resumeStep(["access"], [])).toBe("profile");
    expect(
      resumeStep([], ["access", "profile", "building", "looking", "where"]),
    ).toBe("sites");
    // "Websites to search" is done once the list holds a place to search.
    expect(
      resumeStep(
        ["looking", "where", "work"],
        ["access", "profile", "building"],
      ),
    ).toBe("sites");
    expect(
      resumeStep(
        ["looking", "where", "sites", "work"],
        ["access", "profile", "building"],
      ),
    ).toBe("pay");
    expect(
      resumeStep(
        [],
        passedOf(["profile", "looking", "where", "work", "pay", "apply"]),
      ),
    ).toBe("summary");
  });
  it("reads the steps passed from the profile, each once, in order", () => {
    expect(passedOf(["where", "nope", "profile", "where"])).toEqual([
      "access",
      "profile",
      "building",
      "where",
    ]);
  });
  it("counts Model access, Building your profile and Websites to search as passed once a later step is, never sent to the api", () => {
    expect(passedOf(["profile"])).not.toContain("building");
    expect(passedOf(["profile", "looking", "where"])).not.toContain("sites");
    expect(passedOf(["where", "work"])).toEqual([
      "access",
      "building",
      "where",
      "sites",
      "work",
    ]);
    expect(UNSTORED.has("access")).toBe(true);
    expect(UNSTORED.has("building")).toBe(true);
    expect(UNSTORED.has("sites")).toBe(true);
  });
  it("opens the step asked for, never one after the roles while none is saved", () => {
    expect(openStep("pay", ["looking"], [])).toBe("pay");
    expect(openStep(null, [], ["access", "profile", "building"])).toBe(
      "looking",
    );
    // typed in the address, with no role saved: the first step left instead
    expect(openStep("summary", [], ["access", "profile", "building"])).toBe(
      "looking",
    );
    expect(openStep("apply", [], [])).toBe("access");
    // a role removed since: passing it once is not enough
    expect(
      openStep(
        "summary",
        ["where"],
        ["access", "profile", "building", "looking", "where"],
      ),
    ).toBe("looking");
    expect(openStep("profile", [], [])).toBe("profile");
    expect(
      openStep("nowhere", ["looking"], ["access", "profile", "building"]),
    ).toBe("where");
  });
});

const withAnswers = (patch: Partial<Answers>, search = EMPTY.search) => ({
  ...EMPTY,
  search,
  answers: { ...EMPTY.answers, ...patch },
});
const paid: Search = {
  ...EMPTY.search,
  compensation: {
    salary: { min: 50000, max: 60000, currency: "EUR" },
    daily_rate: { min: 500, max: 600, currency: "EUR" },
  },
};

describe("draftOf", () => {
  it("reads the answers the wizard asks from the stored answers", () => {
    const read = draftOf(
      withAnswers({
        motivation: "Shipping agents.",
        notice_period: "1 month",
        earliest_start: "January 2027",
        work_authorization: [
          { country: "France", answer: "Yes", needs_sponsorship: false },
          { country: "Canada", answer: "No", needs_sponsorship: true },
        ],
      }),
    );
    expect(read).toMatchObject({
      motivation: "Shipping agents.",
      notice: "1 month",
      start: "January 2027",
      authorization: {
        country: "France",
        allowed: "yes",
        sponsorship: "no",
        kept: "Yes",
      },
    });
    const empty = draftOf(EMPTY);
    expect(empty).toMatchObject({ motivation: "", notice: "", start: "" });
    expect(empty.authorization).toEqual({
      country: "",
      allowed: null,
      sponsorship: null,
      kept: "",
    });
  });
  it("reads Rather not say from the stance, only while no pay is saved", () => {
    const blank = { salary_stance: "leave_blank" } as const;
    expect(draftOf(withAnswers(blank)).ratherNotSay).toBe(true);
    expect(draftOf(withAnswers(blank, paid)).ratherNotSay).toBe(false);
    expect(draftOf(EMPTY).ratherNotSay).toBe(false);
  });
});

describe("answersWith", () => {
  const stored: Answers = {
    ...EMPTY.answers,
    eeo_policy: "stored",
    eeo_answers: [{ topic: "gender", answer: "Woman" }],
    relocation: "Open to Lyon.",
    work_authorization: [
      {
        country: "France",
        answer: "Yes, EU citizen.",
        needs_sponsorship: false,
      },
      { country: "Canada", answer: "No", needs_sponsorship: true },
    ],
    salary_stance: "sentence",
    salary_policy: "Against the band.",
    salary_ranges: [
      { currency: "USD", minimum: 90000, maximum: null, period: "year" },
    ],
  };
  const typed = (patch: Partial<Draft> = {}) => ({
    ...draftOf({ ...EMPTY, answers: stored }),
    ...patch,
  });
  const none = EMPTY.search.compensation;

  it("writes the motivation on Looking and keeps every other answer", () => {
    const saved = answersWith(
      "looking",
      stored,
      typed({ motivation: "  Shipping agents.  " }),
      none,
    );
    expect(saved).toEqual({ ...stored, motivation: "Shipping agents." });
    expect(
      answersWith("looking", stored, typed({ motivation: " " }), none)
        .motivation,
    ).toBeNull();
  });
  it("writes the notice, the start and the first country on Work, the others kept", () => {
    const saved = answersWith(
      "work",
      stored,
      typed({
        notice: "1 month",
        start: "As soon as possible",
        authorization: {
          country: "Germany",
          allowed: "yes",
          sponsorship: "no",
          kept: "",
        },
      }),
      none,
    );
    expect(saved).toEqual({
      ...stored,
      notice_period: "1 month",
      earliest_start: "As soon as possible",
      work_authorization: [
        { country: "Germany", answer: "Yes", needs_sponsorship: false },
        { country: "Canada", answer: "No", needs_sponsorship: true },
      ],
    });
  });
  it("keeps the stored words of a country while its yes or no is unchanged", () => {
    const same = answersWith("work", stored, typed(), none);
    expect(same.work_authorization).toEqual(stored.work_authorization);
    const changed = answersWith(
      "work",
      stored,
      typed({
        authorization: { ...typed().authorization, allowed: "no" },
      }),
      none,
    );
    expect(changed.work_authorization[0]).toEqual({
      country: "France",
      answer: "No",
      needs_sponsorship: false,
    });
  });
  it("names each country once, and leaves the list alone when none is typed", () => {
    const moved = answersWith(
      "work",
      stored,
      typed({
        authorization: {
          country: "canada",
          allowed: "yes",
          sponsorship: null,
          kept: "",
        },
      }),
      none,
    );
    expect(moved.work_authorization).toEqual([
      { country: "canada", answer: "Yes", needs_sponsorship: null },
    ]);
    const untouched = answersWith(
      "work",
      stored,
      typed({
        authorization: {
          country: " ",
          allowed: null,
          sponsorship: null,
          kept: "",
        },
      }),
      none,
    );
    expect(untouched.work_authorization).toBe(stored.work_authorization);
  });
  it("gives forms the pay as ranges, one per currency, the yearly one first", () => {
    expect(rangesOf(paid.compensation)).toEqual([
      { currency: "EUR", minimum: 50000, maximum: 60000, period: "year" },
    ]);
    const saved = answersWith(
      "pay",
      stored,
      typed({
        salary: { min: "50000", max: "60000", currency: "EUR" },
        daily: { min: "500", max: "600", currency: "CHF" },
      }),
      none,
    );
    expect(saved.salary_stance).toBe("range");
    expect(saved.salary_ranges).toEqual([
      { currency: "EUR", minimum: 50000, maximum: 60000, period: "year" },
      { currency: "CHF", minimum: 500, maximum: 600, period: "day" },
      { currency: "USD", minimum: 90000, maximum: null, period: "year" },
    ]);
    expect(saved.salary_policy).toBe("Against the band.");
  });
  it("replaces the ranges it wrote before, and keeps the person's own", () => {
    const before = {
      salary: { min: 40000, max: 45000, currency: "GBP" },
      daily_rate: null,
    };
    const saved = answersWith(
      "pay",
      {
        ...stored,
        salary_stance: "range",
        salary_ranges: [
          { currency: "GBP", minimum: 40000, maximum: 45000, period: "year" },
          ...stored.salary_ranges,
        ],
      },
      typed({ salary: { min: "50000", max: "55000", currency: "EUR" } }),
      before,
    );
    expect(saved.salary_ranges).toEqual([
      { currency: "EUR", minimum: 50000, maximum: 55000, period: "year" },
      { currency: "USD", minimum: 90000, maximum: null, period: "year" },
    ]);
  });
  it("leaves the salary blank on Rather not say, every range kept", () => {
    const saved = answersWith(
      "pay",
      stored,
      typed({ ratherNotSay: true }),
      none,
    );
    expect(saved).toEqual({ ...stored, salary_stance: "leave_blank" });
  });
  it("clears the stance Rather not say showed once unticked with no figure, and keeps another", () => {
    const blank = { ...stored, salary_stance: "leave_blank" as const };
    expect(
      answersWith("pay", blank, typed({ ratherNotSay: false }), none)
        .salary_stance,
    ).toBeNull();
    expect(
      answersWith("pay", stored, typed({ ratherNotSay: false }), none),
    ).toEqual(stored);
  });
  it("leaves the answers alone on the other steps", () => {
    expect(answersWith("where", stored, typed(), none)).toBe(stored);
  });
});

describe("nextPage", () => {
  it.each([
    ["/jobs", "/jobs"],
    ["/jobs/42", "/jobs/42"],
    [
      "/jobs?status=found&q=data%20engineer",
      "/jobs?status=found&q=data%20engineer",
    ],
    ["/jobs/map#paris", "/jobs/map#paris"],
    ["/profile/fill?step=review", "/profile/fill?step=review"],
    ["/account", "/account"],
    ["/jobs/./42", "/jobs/42"],
    ["/jobs/../account", "/account"],
    ["/caf%C3%A9", "/caf%C3%A9"],
    ["/jobs?q=a%2Fb%20c#part%20one", "/jobs?q=a%2Fb%20c#part%20one"],
    // a path of this origin, harmless: the router opens a page that does not exist
    ["/javascript:alert(1)//", "/javascript:alert(1)//"],
  ])("keeps the path of this app %j", (value, kept) => {
    expect(nextPage(value)).toBe(kept);
  });

  it.each([
    null,
    "",
    "jobs",
    "https://evil.example/jobs",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\/evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "\t/evil.example",
    "/ /evil.example",
    "/jobs evil",
    "/jobs\u0000",
    "/jobs\u007f",
    "/\u00a0/evil.example",
    "/\u2028/evil.example",
    "/%09/evil.example",
    "/%0a/evil.example",
    "/%0D/evil.example",
    "/%20/evil.example",
    "/%5c/evil.example",
    "/%2F/evil.example",
    "/%2f%2fevil.example",
    "/%E0%A4%A",
    "/welcome",
    "/welcome?step=pay",
    "/welcome/again",
    "/%77elcome",
    "/Welcome",
    "/%57elcome",
    "/wElCoMe?step=pay#summary",
    "/%57ELCOME/again",
    "/WELCOME///",
    "/jobs/../Welcome",
    "javascript:alert(1)",
  ])("refuses %j", (value) => {
    expect(nextPage(value)).toBeNull();
  });
});
