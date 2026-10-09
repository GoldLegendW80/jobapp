import { describe, expect, it } from "vitest";

import { ApiError, parseRetryAfter } from "@/lib/api";

import { formatWait, throttledFor } from "./retry-countdown";

describe("parseRetryAfter", () => {
  it.each([
    [null, undefined],
    ["", undefined],
    ["soon", undefined],
    ["-5", undefined],
    ["1.5", undefined],
    ["0", 0],
    ["900", 900],
    [" 42 ", 42],
  ])("reads %j as %j", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });

  it("turns an HTTP date into the seconds left, 0 when past", () => {
    const now = Date.parse("Fri, 09 Oct 2026 10:00:00 GMT");
    expect(parseRetryAfter("Fri, 09 Oct 2026 10:01:30 GMT", now)).toBe(90);
    expect(parseRetryAfter("Fri, 09 Oct 2026 09:00:00 GMT", now)).toBe(0);
  });
});

describe("throttledFor", () => {
  const http = (status: number, retryAfter?: number) =>
    new ApiError("http", "", { status, retryAfter });

  it("is the Retry-After of a 429 only", () => {
    expect(throttledFor(http(429, 60))).toBe(60);
    expect(throttledFor(http(429))).toBeUndefined();
    expect(throttledFor(http(429, 0))).toBeUndefined();
    expect(throttledFor(http(503, 60))).toBeUndefined();
    expect(throttledFor(new Error("x"))).toBeUndefined();
  });
});

describe("formatWait", () => {
  it.each([
    [0, "0:00"],
    [1, "0:01"],
    [9.2, "0:10"],
    [59, "0:59"],
    [60, "1:00"],
    [65, "1:05"],
    [900, "15:00"],
    [3661, "61:01"],
  ])("formats %d seconds as %s", (seconds, text) => {
    expect(formatWait(seconds)).toBe(text);
  });
});
