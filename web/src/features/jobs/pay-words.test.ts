import { describe, expect, it } from "vitest";

import { payWords, typedPay } from "./pay-words";

describe("payWords", () => {
  it.each([
    [45000, 55000, "EUR", "year", "45,000 to 55,000 EUR a year"],
    [600, 600, "EUR", "day", "600 EUR a day"],
    [60, 75, "USD", "hour", "60 to 75 USD an hour"],
    [4000, 4000, "GBP", "month", "4,000 GBP a month"],
    [10000000, 10000000, "CHF", "year", "10,000,000 CHF a year"],
    [4.35, 5.1, "EUR", "hour", "4.35 to 5.10 EUR an hour"],
    [16.5, 16.5, "USD", "hour", "16.50 USD an hour"],
    [600.5, 600.5, "CHF", "day", "600.50 CHF a day"],
    [1200.5, 1200.5, "GBP", "month", "1,200.50 GBP a month"],
    [120000.75, 120000.75, "CAD", "year", "120,000.75 CAD a year"],
  ])("%d to %d %s per %s", (low, high, currency, period, text) => {
    expect(
      payWords({
        pay_min: low,
        pay_max: high,
        pay_currency: currency,
        pay_period: period,
      }),
    ).toBe(text);
    expect(text).not.toMatch(/ - |–|—/);
  });

  it("is null when the posting states no pay, or an incomplete one", () => {
    expect(payWords({})).toBeNull();
    expect(
      payWords({
        pay_min: null,
        pay_max: null,
        pay_currency: null,
        pay_period: null,
      }),
    ).toBeNull();
    expect(
      payWords({ pay_min: 600, pay_max: 600, pay_currency: "EUR" }),
    ).toBeNull();
    expect(
      payWords({
        pay_min: 600,
        pay_max: 600,
        pay_currency: "EUR",
        pay_period: "week",
      }),
    ).toBeNull();
  });
});

describe("typedPay", () => {
  it.each([
    ["60000", "70000", { min: 60000, max: 70000 }],
    ["600", "", { min: 600, max: 600 }],
    ["", "600", { min: 600, max: 600 }],
    [" 600 ", " 650 ", { min: 600, max: 650 }],
    ["1", "10000000", { min: 1, max: 10000000 }],
  ])("%s to %s is a pay", (low, high, figures) => {
    expect(typedPay(low, high, "EUR", "day")).toEqual({
      ...figures,
      currency: "EUR",
      period: "day",
    });
  });

  it("is null for no figure at all", () => {
    expect(typedPay("", " ", "EUR", "year")).toBeNull();
  });

  it.each([
    ["70000", "60000"],
    ["0", "10"],
    ["1", "10000001"],
    ["60k", ""],
    ["60,000", ""],
    ["-5", "10"],
    ["1.5", "2"],
  ])("%s to %s is refused", (low, high) => {
    expect(typedPay(low, high, "EUR", "year")).toBeUndefined();
  });

  it("needs a currency and a known period", () => {
    expect(typedPay("600", "", "", "day")).toBeUndefined();
    expect(typedPay("600", "", "EUR", "week")).toBeUndefined();
  });
});
