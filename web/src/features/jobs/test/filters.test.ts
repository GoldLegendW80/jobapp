import { describe, expect, it } from "vitest";

import { activeFilterChips } from "../helpers/filter-chips";
import {
  activeFilterCount,
  EMPTY_FILTERS,
  foundToday,
  hasFilters,
  hideSkippedAndClosed,
  localIsoDate,
  isIsoDate,
  parseTypedDate,
  parseFilters,
  TEXT_MAX,
  toggleFitRange,
  toggleOnlyStatus,
  toSearchParams,
  type JobFilters,
} from "../helpers/filters";

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
      places: ["La Réunion", "Paris"],
      workModes: ["remote", "not_stated"],
      sources: ["linkedin", "web"],
      publishedWithin: 7,
      publishedFrom: "2026-11-01",
      publishedUndated: true,
      pay: "stated",
      payMin: 50000,
      payPeriod: "year",
      applications: ["none", "failed"],
      sweep: "0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa",
      sort: "fit",
    };
    expect(roundTrip(filters)).toEqual(filters);
    expect(toSearchParams(filters).toString()).toBe(
      "q=forward+deployed&status=shortlisted&status=cv_ready&type=CDI&type=VIE&company=Mistral&fit_min=70&fit_max=100&found_from=2026-09-01&found_to=2026-10-09" +
        "&place=La+R%C3%A9union&place=Paris&work_mode=remote&work_mode=not_stated&source=linkedin&source=web&published_within=7&published_from=2026-11-01&published_undated=true" +
        "&pay=stated&pay_min=50000&pay_period=year&application=none&application=failed&sweep=0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa&sort=fit",
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
    ["sort=published", { sort: "published" }],
    ["sort=pay", { sort: "pay" }],
    ["place=Saint-Denis", { places: ["Saint-Denis"] }],
    ["work_mode=hybrid", { workModes: ["hybrid"] }],
    [
      "source=www.welcometothejungle.com",
      { sources: ["www.welcometothejungle.com"] },
    ],
    ["published_within=0", { publishedWithin: 0 }],
    ["published_from=2026-11-20", { publishedFrom: "2026-11-20" }],
    ["pay=not_stated", { pay: "not_stated" }],
    ["pay_min=600&pay_period=day", { payMin: 600, payPeriod: "day" }],
    ["application=waiting", { applications: ["waiting"] }],
    [
      "sweep=0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa",
      { sweep: "0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa" },
    ],
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

describe("parseTypedDate", () => {
  it.each([
    ["", null],
    ["  ", null],
    ["2026-11-06", "2026-11-06"],
    ["6 Nov 2026", "2026-11-06"],
    ["06 november 2026", "2026-11-06"],
    ["30 Sept 2026", "2026-09-30"],
    ["1 Mar. 2026", "2026-03-01"],
    ["31 Feb 2026", false],
    ["6 No 2026", false],
    ["6 Nob 2026", false],
    ["06/11/2026", false],
    ["yesterday", false],
  ])("%j reads %j", (value, expected) => {
    expect(parseTypedDate(value)).toBe(expected);
  });
});

describe("the filters of jobs J29 in the URL", () => {
  it.each<[string, string, Partial<JobFilters>]>([
    [
      "a place without a letter",
      "place=%2C%2C&place=Paris",
      { places: ["Paris"] },
    ],
    ["a place too long", `place=${"x".repeat(101)}`, {}],
    ["a place twice", "place=Paris&place=Paris", { places: ["Paris"] }],
    [
      "more than 20 places",
      Array.from({ length: 25 }, (_, i) => `place=p${String(i)}`).join("&"),
      { places: Array.from({ length: 20 }, (_, i) => `p${String(i)}`) },
    ],
    [
      "an unknown work mode",
      "work_mode=anywhere&work_mode=office",
      { workModes: ["office"] },
    ],
    ["a blank source", "source=%20", {}],
    ["days out of range", "published_within=3651", {}],
    ["negative days", "published_within=-1", {}],
    ["a false date", "published_from=2026-02-31", {}],
    [
      "keep undated alone",
      "published_undated=true",
      { publishedUndated: true },
    ],
    ["a pay minimum alone", "pay_min=50000", {}],
    ["a period alone", "pay_period=year", {}],
    ["a minimum of 0", "pay_min=0&pay_period=year", {}],
    [
      "not stated with a minimum",
      "pay=not_stated&pay_min=5&pay_period=year",
      { pay: "not_stated" },
    ],
    [
      "an unknown application",
      "application=maybe&application=sent",
      { applications: ["sent"] },
    ],
    ["a sweep that is no id", "sweep=abc", {}],
  ])("drops %s", (_, query, expected) => {
    expect(parse(query)).toEqual({ ...EMPTY_FILTERS, ...expected });
  });

  it("sends keep undated only with a published bound", () => {
    expect(toSearchParams(parse("published_undated=true")).toString()).toBe("");
    expect(hasFilters(parse("published_undated=true"))).toBe(false);
  });

  it("counts one per value and one per range, as the chips", () => {
    const filters = parse(
      "place=Paris&place=Lyon&work_mode=remote&source=web&published_within=7&pay=stated&application=sent&sweep=0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa",
    );
    expect(activeFilterCount(filters)).toBe(8);
    expect(activeFilterChips(filters)).toHaveLength(8);
  });

  it("names each chip and removes only its own filter", () => {
    const filters = parse(
      "q=ml&place=Saint-Denis&work_mode=not_stated&type=CDI&source=web&published_within=7&published_undated=true&pay_min=50000&pay_period=year&fit_min=60&application=waiting&status=found&found_from=2026-11-26&found_to=2026-11-26&company=Acme&sweep=0b6c3a52-6d1e-4c4f-9f43-2a41c1a1e0aa",
    );
    const chips = activeFilterChips(filters);
    expect(chips.map((chip) => chip.text)).toEqual([
      "Search: ml",
      "Place: Saint-Denis",
      "Work mode: Not stated",
      "Contract: CDI",
      "Website: Anywhere on the web",
      "Published in the last 7 days, or no date",
      "Pay: at least 50,000 a year",
      "Fit: 60 to 100",
      "Application: Waiting for you",
      "Status: Found",
      "Found on 26 Nov 2026",
      "Company: Acme",
      "Added by one search",
    ]);
    const published = chips.find((chip) => chip.key === "published")!;
    expect(published.without).toEqual({
      ...filters,
      publishedWithin: null,
      publishedFrom: null,
      publishedUndated: false,
    });
    const place = chips.find((chip) => chip.key === "places-Saint-Denis")!;
    expect(place.without.places).toEqual([]);
    expect(place.without.sweep).toBe(filters.sweep);
  });

  it("applies the presets over the other filters", () => {
    const filters = parse("place=Paris&status=skipped");
    expect(foundToday(filters, "2026-11-26")).toEqual({
      ...filters,
      foundFrom: "2026-11-26",
      foundTo: "2026-11-26",
    });
    expect(hideSkippedAndClosed(filters).statuses).toEqual([
      "found",
      "shortlisted",
      "cv_ready",
      "staged",
      "applied",
      "interview",
      "rejected",
    ]);
    expect(localIsoDate(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});
