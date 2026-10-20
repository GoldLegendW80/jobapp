import { afterEach, describe, expect, it } from "vitest";

import { EMPTY, type Search } from "@/features/profile/api";

import {
  draftOf,
  readPassed,
  readPay,
  resumeStep,
  searchWith,
  splitTerms,
  stepProblem,
  writePassed,
  type Draft,
} from "./steps";

const draft = (patch: Partial<Draft> = {}): Draft => ({
  ...draftOf(EMPTY),
  ...patch,
});

afterEach(() => {
  window.localStorage.clear();
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
});

describe("searchWith", () => {
  const stored: Search = {
    ...EMPTY.search,
    min_fit: 55,
    boards: [
      {
        company: "Example Robotics",
        ats: "ashby",
        org: "example-robotics",
        enabled: true,
        extra_exclude: { terms: [], advanced_regex: false },
      },
    ],
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
    expect(saved.boards).toEqual(stored.boards);
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
    expect(resumeStep([], [])).toBe("profile");
    expect(resumeStep([], ["profile", "looking", "where"])).toBe("work");
    expect(resumeStep(["looking", "where", "work"], ["profile"])).toBe("pay");
    expect(
      resumeStep([], ["profile", "looking", "where", "work", "pay", "apply"]),
    ).toBe("summary");
  });
  it("keeps each person's steps apart", () => {
    writePassed("person-a", ["profile", "looking", "where"]);
    expect(readPassed("person-a")).toEqual(["profile", "looking", "where"]);
    expect(readPassed("person-b")).toEqual([]);
  });
  it("reads damaged storage as nothing passed", () => {
    window.localStorage.setItem("welcome:v1:p", "{not json");
    expect(readPassed("p")).toEqual([]);
    window.localStorage.setItem(
      "welcome:v1:p",
      JSON.stringify(["where", "nope", 3]),
    );
    expect(readPassed("p")).toEqual(["where"]);
  });
});
