import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { fieldError } from "./field-error";

const http = (detail: unknown, status = 422) =>
  new ApiError("http", "x", { status, body: { detail } });
const FIELDS = ["current_password", "new_password"] as const;

describe("fieldError", () => {
  it("splits a field prefixed detail", () => {
    expect(
      fieldError(http("new_password: Use at least 10 characters."), FIELDS),
    ).toEqual({
      field: "new_password",
      message: "Use at least 10 characters.",
    });
  });

  it("keeps colons inside the message", () => {
    expect(fieldError(http("current_password: a: b"), FIELDS)?.message).toBe(
      "a: b",
    );
  });

  it.each([
    http("name: Enter your name."), // a field this form does not have
    http("Something else entirely."),
    http(""),
    http(42),
    new ApiError("network", "down"),
    new Error("plain"),
    undefined,
  ])("is undefined for %#", (error) => {
    expect(fieldError(error, FIELDS)).toBeUndefined();
  });
});
