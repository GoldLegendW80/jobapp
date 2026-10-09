import { describe, expect, it } from "vitest";

import {
  EMPTY_FILTERS,
  hasFilters,
  isIsoDate,
  parseFilters,
  TEXT_MAX,
  toggleFitRange,
  toggleOnlyStatus,
  toSearchParams,
  type JobFilters,
} from "./filters";

const parse = (query: string) => parseFilters(new URLSearchParams(query));
const roundTrip = (filters: JobFilters) =>
  parseFilters(toSearchParams(filters));

describe("job filters in the URL", () => {
  it("parses an empty query to no filters and the default sort", () => {
    expect(parse("")).toEqual(EMPTY_FILTERS);
    expect(toSearchParams(EMPTY_FILTERS).toString()).toBe("");
    expect(hasFilters(EMPTY_FILTERS)).toBe(false);
  });

  it("round trips every filter at once", () => {
    const filters: JobFilters = {
      statuses: ["shortlisted", "cv_ready"],
      types: ["CDI", "VIE"],
      q: "forward deployed",
      company: "Mistral",
      fitMin: 70,
      fitMax: 100,
      foundFrom: "2026-09-01",
      foundTo: "2026-10-09",
      sort: "fit",
    };
    expect(roundTrip(filters)).toEqual(filters);
    expect(toSearchParams(filters).toString()).toBe(
      "q=forward+deployed&status=shortlisted&status=cv_ready&type=CDI&type=VIE&company=Mistral&fit_min=70&fit_max=100&found_from=2026-09-01&found_to=2026-10-09&sort=fit",
    );
  });

  it.each<[string, Partial<JobFilters>]>([
    ["status=applied", { statuses: ["applied"] }],
    ["type=Freelance", { types: ["Freelance"] }],
    ["q=agents", { q: "agents" }],
    ["company=Dust", { company: "Dust" }],
    ["fit_min=0", { fitMin: 0 }],
    ["fit_max=100", { fitMax: 100 }],
    ["found_from=2026-01-31", { foundFrom: "2026-01-31" }],
    ["found_to=2024-02-29", { foundTo: "2024-02-29" }],
    ["sort=company", { sort: "company" }],
    ["sort=updated", { sort: "updated" }],
    ["sort=status", { sort: "status" }],
  ])("round trips %s alone", (query, expected) => {
    const filters = parse(query);
    expect(filters).toEqual({ ...EMPTY_FILTERS, ...expected });
    expect(toSearchParams(filters).toString()).toBe(query);
  });

  it("keeps statuses and types once each, in pipeline order", () => {
    const filters = parse(
      "status=skipped&status=found&status=skipped&type=VIE&type=CDI&type=VIE",
    );
    expect(filters.statuses).toEqual(["found", "skipped"]);
    expect(filters.types).toEqual(["CDI", "VIE"]);
  });

  it.each([
    ["an unknown status", "status=hired&status=found", { statuses: ["found"] }],
    ["a status in the wrong case", "status=Found", {}],
    ["an unknown type", "type=Interim", {}],
    ["an unknown sort", "sort=random", {}],
    ["the default sort", "sort=found_on", {}],
    ["a fit above 100", "fit_min=140", {}],
    ["a negative fit", "fit_min=-5", {}],
    ["a fractional fit", "fit_max=7.5", {}],
    ["a fit that is not a number", "fit_max=abc", {}],
    ["an empty fit", "fit_min=", {}],
    ["a fit with an exponent", "fit_min=1e2", {}],
    ["min above max (both dropped)", "fit_min=80&fit_max=20", {}],
    ["a date that does not exist", "found_from=2026-02-31", {}],
    ["a date in another format", "found_to=09/10/2026", {}],
    ["a date with a time", "found_to=2026-10-09T10:00", {}],
    [
      "dates the wrong way round (both dropped)",
      "found_from=2026-10-09&found_to=2026-01-01",
      {},
    ],
    ["blank search", "q=%20%20%20", {}],
  ])("ignores %s safely", (_name, query, expected) => {
    expect(parse(query)).toEqual({ ...EMPTY_FILTERS, ...expected });
  });

  it("keeps a valid filter next to an invalid one", () => {
    expect(parse("fit_min=60&fit_max=abc&status=nope&type=CDD")).toEqual({
      ...EMPTY_FILTERS,
      fitMin: 60,
      types: ["CDD"],
    });
  });

  it("trims search text and cuts it at the api limit", () => {
    expect(parse("q=%20%20llm%20%20").q).toBe("llm");
    expect(parse(`company=${"x".repeat(500)}`).company).toHaveLength(TEXT_MAX);
  });

  it("keeps unicode and characters with a meaning in URLs", () => {
    const filters = {
      ...EMPTY_FILTERS,
      q: 'développeur "IA" & co #1 +50%',
      company: "Ångström/Zoë?",
    };
    expect(roundTrip(filters)).toEqual(filters);
  });

  it("drops unknown parameters from the canonical query", () => {
    expect(
      toSearchParams(parse("utm_source=mail&status=found")).toString(),
    ).toBe("status=found");
  });

  it("counts the sort as no filter", () => {
    expect(hasFilters({ ...EMPTY_FILTERS, sort: "fit" })).toBe(false);
    expect(hasFilters({ ...EMPTY_FILTERS, fitMin: 0 })).toBe(true);
  });
});

describe("toggles", () => {
  it("a KPI click selects that status alone, a second click clears it", () => {
    const many = {
      ...EMPTY_FILTERS,
      statuses: ["found", "applied"] as JobFilters["statuses"],
    };
    const one = toggleOnlyStatus(many, "applied");
    expect(one.statuses).toEqual(["applied"]);
    expect(toggleOnlyStatus(one, "applied").statuses).toEqual([]);
    expect(toggleOnlyStatus(one, "found").statuses).toEqual(["found"]);
  });

  it("a bucket click sets the fit range, a second click clears it", () => {
    const set = toggleFitRange(EMPTY_FILTERS, 80, 89);
    expect([set.fitMin, set.fitMax]).toEqual([80, 89]);
    const cleared = toggleFitRange(set, 80, 89);
    expect([cleared.fitMin, cleared.fitMax]).toEqual([null, null]);
    const other = toggleFitRange(set, 90, 100);
    expect([other.fitMin, other.fitMax]).toEqual([90, 100]);
  });
});

describe("isIsoDate", () => {
  it.each([
    ["2026-10-09", true],
    ["2024-02-29", true],
    ["2026-02-29", false],
    ["2026-13-01", false],
    ["2026-00-10", false],
    ["26-10-09", false],
    ["", false],
  ])("%s is %s", (value, expected) => {
    expect(isIsoDate(value)).toBe(expected);
  });
});
