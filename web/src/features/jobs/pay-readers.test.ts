import { describe, expect, it } from "vitest";

import { makeDetail } from "@/test/jobs";

import { jobDetailSchema, jobListItemSchema } from "./api";

describe("posting pay response decoder", () => {
  it.each([1, 4.35, 16.5, 45000, 10000000])("reads numeric %s", (amount) => {
    const job = makeDetail({
      pay_min: amount,
      pay_max: amount,
      pay_currency: "EUR",
      pay_period: "hour",
    });
    expect(jobListItemSchema.parse(job).pay_min).toBe(amount);
    expect(jobDetailSchema.parse(JSON.parse(JSON.stringify(job))).pay_min).toBe(
      amount,
    );
  });

  it.each([
    true,
    "4.35",
    "45000",
    0,
    -1,
    0.99,
    10000000.01,
    4.351,
    NaN,
    Infinity,
  ])("refuses invalid numeric pay %s", (amount) => {
    const job = { ...makeDetail(), pay_min: amount, pay_max: amount };
    expect(jobListItemSchema.safeParse(job).success).toBe(false);
    expect(jobDetailSchema.safeParse(job).success).toBe(false);
  });

  it("keeps null and omitted pay compatible", () => {
    expect(
      jobDetailSchema.parse(makeDetail({ pay_min: null, pay_max: null }))
        .pay_min,
    ).toBeNull();
    const job = makeDetail();
    delete job.pay_min;
    delete job.pay_max;
    expect(jobDetailSchema.parse(job).pay_min).toBeUndefined();
  });
});
