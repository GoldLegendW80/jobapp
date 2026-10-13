import { describe, expect, it } from "vitest";

import {
  describePeriod,
  endsBeforeStart,
  formatPeriod,
  isPeriod,
  parsePeriod,
  periodKey,
  periodSpan,
  typedPeriod,
} from "./period";

/** The same tables as api/tests/profile/test_schemas.py, so the two sides cannot drift apart. */
const VALID = [
  "2021",
  "2021-03",
  "1900",
  "2100",
  "1900-01",
  "2100-12",
  "2021-01",
  "2021-12",
];
const INVALID = [
  "",
  "2021-3",
  "21",
  "2021-13",
  "2021-00",
  "1899",
  "2101",
  "1899-12",
  "2101-01",
  "2021/03",
  "2021.03",
  "2021-03-01",
  "03-2021",
  "03/2021",
  "2021-",
  "-03",
  "20211",
  "202",
  "2021 03",
  "2021-03-",
  "March 2021",
  "2021-Q1",
  "2021-2023",
  "present",
  "+2021",
  "2021.0",
  "0x7e5",
  " 2021",
  "2021-03 ",
  "２０２１",
];

describe("a period is a month or a year alone", () => {
  it.each(VALID)("%j is a period", (value) => {
    expect(isPeriod(value)).toBe(true);
  });

  it.each(INVALID)("%j is not a period", (value) => {
    expect(isPeriod(value)).toBe(false);
    expect(parsePeriod(value)).toBeNull();
    expect(periodKey(value)).toBeNull();
    expect(periodSpan(value)).toBeNull();
    expect(formatPeriod(value)).toBeNull();
  });

  it("reads the year, and the month only when there is one", () => {
    expect(parsePeriod("2021")).toEqual({ year: 2021, month: null });
    expect(parsePeriod("2021-03")).toEqual({ year: 2021, month: 3 });
  });
});

describe("periodKey", () => {
  const byKey = (a: string, b: string) => {
    const [yearA, monthA] = periodKey(a) ?? [0, 0];
    const [yearB, monthB] = periodKey(b) ?? [0, 0];
    return yearA - yearB || monthA - monthB;
  };

  it("is [year, month], month 0 for a year alone", () => {
    expect(periodKey("2021")).toEqual([2021, 0]);
    expect(periodKey("2021-03")).toEqual([2021, 3]);
  });

  it("orders a year alone before the months of its year", () => {
    const values = [
      "2022",
      "2021-12",
      "2021",
      "2020-12",
      "2021-01",
      "2022-01",
      "2021-06",
    ];
    expect([...values].sort(byKey)).toEqual([
      "2020-12",
      "2021",
      "2021-01",
      "2021-06",
      "2021-12",
      "2022",
      "2022-01",
    ]);
    // Newest first, the way a list of employers reads: a year alone after its months.
    expect(
      ["2021", "2021-03", "2020", "2021-11"].sort((a, b) => byKey(b, a)),
    ).toEqual(["2021-11", "2021-03", "2021", "2020"]);
  });

  it("never gives two different periods the same key", () => {
    const keys = new Set<string>();
    let count = 0;
    for (let year = 1900; year <= 2100; year += 1) {
      const values = [
        String(year),
        ...Array.from(
          { length: 12 },
          (_, m) => `${year}-${String(m + 1).padStart(2, "0")}`,
        ),
      ];
      for (const value of values) {
        keys.add((periodKey(value) ?? []).join("."));
        count += 1;
      }
    }
    expect(keys.size).toBe(count);
  });
});

describe("periodSpan and endsBeforeStart", () => {
  it("gives the first and last month a period can mean", () => {
    expect(periodSpan("2021")).toEqual([2021 * 12, 2021 * 12 + 11]);
    expect(periodSpan("2021-01")).toEqual([2021 * 12, 2021 * 12]);
    expect(periodSpan("2021-12")).toEqual([2021 * 12 + 11, 2021 * 12 + 11]);
  });

  // start, end, is the end certainly before the start
  it.each([
    ["2023-05", "2023", false],
    ["2023", "2022-12", true],
    ["2023", "2023", false],
    ["2023-06", "2023-05", true],
    ["2021", "2021-03", false],
    ["2021-03", "2021", false],
    ["2023-12", "2023", false],
    ["2024-01", "2023", true],
    ["2023", "2023-01", false],
    ["2023", "2024", false],
    ["2024", "2023", true],
    ["2023-05", "2023-05", false],
    ["2023-05", "2023-06", false],
    ["1900", "2100", false],
    ["2100", "1900", true],
    ["1999-12", "2000", false],
    ["2000", "1999-12", true],
    ["2000-01", "1999", true],
  ])("start %j, end %j: refused %s", (start, end, refused) => {
    expect(endsBeforeStart(start, end)).toBe(refused);
  });

  it("does not judge text that is not a period: the format message does", () => {
    expect(endsBeforeStart("2023", "")).toBe(false);
    expect(endsBeforeStart("", "2021")).toBe(false);
    expect(endsBeforeStart("2023", "2021-3")).toBe(false);
  });

  it("is not a comparison of text", () => {
    // As text a year alone sorts before any month of its year.
    expect(["2023-05", "2023"].sort()).toEqual(["2023", "2023-05"]);
    expect(endsBeforeStart("2023-05", "2023")).toBe(false);
  });
});

describe("formatPeriod and describePeriod", () => {
  it("shows a year alone as the year, never with a month", () => {
    expect(formatPeriod("2021")).toBe("2021");
    expect(formatPeriod("2021-01")).toBe("January 2021");
    expect(formatPeriod("2021-03")).toBe("March 2021");
    expect(formatPeriod("1999-12")).toBe("December 1999");
    expect(describePeriod("2021")).toBe("Read as the year 2021.");
    expect(describePeriod("2021")).not.toMatch(/January/);
  });

  it("says how a month is read, and the two forms until the text is one", () => {
    expect(describePeriod(" 2021-03 ")).toBe("Read as March 2021.");
    for (const typed of ["", "20", "2021-", "2021-3", "1899"])
      expect(describePeriod(typed)).toBe(
        "A year alone or a month: 2021 or 2021-03.",
      );
  });
});

describe("typedPeriod", () => {
  it("leaves a year alone as typed", () => {
    expect(typedPeriod("202", "2021")).toBe("2021");
    expect(typedPeriod("", "2021")).toBe("2021");
  });

  it("puts the hyphen before a fifth digit typed after the year", () => {
    expect(typedPeriod("2021", "20210")).toBe("2021-0");
    expect(typedPeriod("2021-0", "2021-03")).toBe("2021-03");
    expect(typedPeriod("", "202103")).toBe("2021-03");
  });

  it("changes nothing else: a typed hyphen, a deletion, other text", () => {
    expect(typedPeriod("2021", "2021-")).toBe("2021-");
    expect(typedPeriod("2021-03", "202103")).toBe("202103");
    expect(typedPeriod("2021-0", "2021-")).toBe("2021-");
    expect(typedPeriod("", "2021-3")).toBe("2021-3");
    expect(typedPeriod("", "2021032")).toBe("2021032");
    expect(typedPeriod("12", "12a45")).toBe("12a45");
  });
});
