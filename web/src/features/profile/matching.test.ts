/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  cleanTerms,
  compileTerms,
  escapeTerm,
  findMatches,
  hasNestedQuantifier,
  matches,
  pyToJs,
  regexProblem,
  termProblem,
} from "./matching";

interface Fixture {
  matching: {
    terms: string[];
    advanced_regex: boolean;
    text: string;
    expected: boolean;
  }[];
  nested_quantifier: { pattern: string; expected: boolean }[];
  check_regex: { pattern: string; valid: boolean }[];
}

// The same file api/tests/profile/test_matching_parity.py checks against the Python code.
const fixture = JSON.parse(
  readFileSync(
    path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../api/tests/profile/fixtures/matching_parity.json",
    ),
    "utf8",
  ),
) as Fixture;

const label = (value: unknown) => JSON.stringify(value).slice(0, 60);

describe("parity with api/app/profile/matching.py", () => {
  it.each(fixture.matching.map((c) => [label([c.terms, c.text]), c] as const))(
    "%s",
    (_name, c) => {
      expect(matches(c.terms, c.advanced_regex, c.text)).toBe(c.expected);
    },
  );

  it("covers plain and advanced terms, matches and misses", () => {
    expect(new Set(fixture.matching.map((c) => c.expected))).toEqual(
      new Set([true, false]),
    );
    expect(fixture.matching.some((c) => c.advanced_regex)).toBe(true);
    expect(fixture.matching.length).toBeGreaterThan(50);
  });
});

describe("parity with schemas.py has_nested_quantifier", () => {
  it.each(fixture.nested_quantifier.map((c) => [label(c.pattern), c] as const))(
    "%s",
    (_name, c) => {
      expect(hasNestedQuantifier(c.pattern)).toBe(c.expected);
    },
  );
});

// Patterns the browser cannot judge: Python refuses them, the browser leaves them to the server
// (a forward reference JavaScript accepts, and `(?<name>)`, which only JavaScript knows).
const SERVER_ONLY = new Set(["\\1(a)", "(?<n>a)"]);

describe("parity with schemas.py check_regex", () => {
  it.each(fixture.check_regex.map((c) => [label(c.pattern), c] as const))(
    "%s",
    (_name, c) => {
      const problem = regexProblem(c.pattern);
      if (c.valid) {
        // Never refuse what the server accepts.
        expect(problem).toBeUndefined();
      } else if (SERVER_ONLY.has(c.pattern)) {
        expect(problem).toBeUndefined();
      } else {
        expect(problem).toBeDefined();
      }
    },
  );
});

describe("pyToJs", () => {
  it.each([
    ["(?P<w>ab)(?P=w)", "(?<w>ab)\\k<w>"],
    ["a{,3}", "a{0,3}"],
    ["x{", "x\\{"],
    ["]", "\\]"],
    ["[]a]", "[\\]a]"],
    ["\\-", "-"],
    ["\\/", "\\/"],
    ["\\ ", " "],
    ["\\é", "é"],
    ["\\a", "\\x07"],
    ["(?i:x)", "(?:x)"],
    ["[\\-x]", "[\\-x]"],
    ["[\\.x]", "[.x]"],
  ])("%s becomes %s", (python, js) => {
    expect(pyToJs(python)).toBe(js);
  });

  it.each([
    "a++",
    "a*+",
    "a?+",
    "a{2}+",
    "(?>a)",
    "(?#c)",
    "(?(1)a|b)",
    "(?i)a",
    "(?m)a",
    "(?x) a",
    "(?<n>a)",
    "\\N{DIGIT ONE}",
    "\\0",
    "[\\W]",
    "[\\1]",
  ])("%s cannot be reproduced", (python) => {
    expect(pyToJs(python)).toBeNull();
  });

  it("keeps an invalid pattern invalid", () => {
    for (const bad of ["(abc", "[a-", "*a", "a{2,1}", "\\", "a)", "\\q"]) {
      const source = pyToJs(bad);
      expect(source).not.toBeNull();
      expect(() => new RegExp(source ?? "", "u")).toThrow();
    }
  });
});

describe("compileTerms and findMatches", () => {
  it("counts every occurrence, as findall does", () => {
    const compiled = compileTerms(["agent", "llm"], false);
    expect(compiled.kind).toBe("ok");
    if (compiled.kind !== "ok") return;
    expect(
      findMatches(compiled.regex, "Agents, an agent, LLMs; agentic no."),
    ).toEqual(["Agents", "agent", "LLMs"]);
  });

  it("reports empty, unsupported and invalid lists", () => {
    expect(compileTerms([], true)).toEqual({ kind: "empty" });
    expect(compileTerms(["a++"], true)).toEqual({ kind: "unsupported" });
    const invalid = compileTerms(["(a"], true);
    expect(invalid.kind).toBe("invalid");
    expect(matches(["(a"], true, "a")).toBeUndefined();
    expect(matches(["a++"], true, "aa")).toBeUndefined();
  });

  it("stops after a thousand matches", () => {
    const compiled = compileTerms(["a"], true);
    if (compiled.kind !== "ok") throw new Error("expected ok");
    expect(findMatches(compiled.regex, "a".repeat(5000))).toHaveLength(1000);
  });

  it("escapes every metacharacter of a plain term", () => {
    expect(escapeTerm("a.b*c+(d)[e]{f}|g^h$i?j\\k")).toBe(
      "a\\.b\\*c\\+\\(d\\)\\[e\\]\\{f\\}\\|g\\^h\\$i\\?j\\\\k",
    );
    expect(matches(["a.b*c"], false, "x a.b*c y")).toBe(true);
  });
});

describe("termProblem", () => {
  it("checks plain terms", () => {
    expect(termProblem("agent", false)).toBeUndefined();
    expect(termProblem("   ", false)).toBe("Enter a term.");
    expect(termProblem("a\u0007b", false)).toBe(
      "Remove the control characters.",
    );
    expect(termProblem("x".repeat(200), false)).toBeUndefined();
    expect(termProblem("x".repeat(201), false)).toBe(
      "A plain term is at most 200 characters.",
    );
    // A plain term is never a pattern: brackets are fine.
    expect(termProblem("(a+)+", false)).toBeUndefined();
  });

  it("checks advanced terms like the server", () => {
    expect(termProblem("\\bllm\\b", true)).toBeUndefined();
    expect(termProblem("x".repeat(500), true)).toBeUndefined();
    expect(termProblem("x".repeat(501), true)).toBe(
      "Regular expression longer than 500 characters.",
    );
    expect(termProblem("(a+)+", true)).toMatch(/^Nested quantifiers/);
    expect(termProblem("(abc", true)).toMatch(/^Invalid regular expression: /);
    expect(termProblem("a++", true)).toBeUndefined(); // the server decides
  });
});

describe("cleanTerms", () => {
  it("trims, drops empties and case-insensitive duplicates, keeps the first", () => {
    expect(cleanTerms([" Agent ", "agent", "", "LLM", "llm ", "Café"])).toEqual(
      ["Agent", "LLM", "Café"],
    );
  });

  it("normalises to NFC", () => {
    expect(cleanTerms(["café", "café"])).toEqual(["café"]);
  });
});
