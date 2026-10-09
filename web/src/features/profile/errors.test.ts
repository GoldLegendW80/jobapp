import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { haraldLike } from "@/test/profile";

import { serverFieldError, validationDetail } from "./errors";
import { toAnswersForm, toExperienceForm, toIdentityForm } from "./forms";

const identity = toIdentityForm(haraldLike().identity);
const experience = toExperienceForm(haraldLike().experience);
const answers = toAnswersForm(haraldLike().answers);

describe("serverFieldError", () => {
  it("maps a leaf path to that field, as a sentence", () => {
    expect(
      serverFieldError("data.email: must be a valid email address", identity),
    ).toEqual({
      name: "email",
      message: "Must be a valid email address.",
    });
    expect(
      serverFieldError(
        "data.employers.2.start: must be a month written YYYY-MM between 1900 and 2100",
        experience,
      ),
    ).toEqual({
      name: "employers.2.start",
      message: "Must be a month written YYYY-MM between 1900 and 2100.",
    });
    expect(
      serverFieldError("data.languages.1.name: Field required", identity)?.name,
    ).toBe("languages.1.name");
  });

  it("maps an item level end before start to the item's end", () => {
    expect(
      serverFieldError(
        "data.employers.0: end must not be before start",
        experience,
      ),
    ).toEqual({
      name: "employers.0.end",
      message: "End must not be before start.",
    });
    expect(
      serverFieldError(
        "data.education.1: end must not be before start",
        experience,
      )?.name,
    ).toBe("education.1.end");
  });

  it("maps a duplicate in a list to the first repeated item", () => {
    const dup = {
      ...experience,
      employers: [
        experience.employers[0]!,
        experience.employers[1]!,
        { ...experience.employers[2]!, key: "betclic" },
      ],
    };
    expect(
      serverFieldError(
        "data.employers: duplicate employer key: each must be unique",
        dup,
        {
          unique: { employers: "key" },
        },
      ),
    ).toEqual({
      name: "employers.2.key",
      message: "Duplicate employer key: each must be unique.",
    });
    const countries = {
      ...answers,
      work_authorization: [
        answers.work_authorization[0]!,
        { ...answers.work_authorization[1]!, country: " france " },
      ],
    };
    expect(
      serverFieldError(
        "data.work_authorization: duplicate country: each must be unique",
        countries,
        {
          unique: { work_authorization: "country" },
        },
      )?.name,
    ).toBe("work_authorization.1.country");
  });

  it("maps a plain string list item to its value field", () => {
    expect(
      serverFieldError(
        "data.leave_blank.0: control characters are not allowed",
        answers,
        {
          wrapped: ["leave_blank"],
        },
      ),
    ).toEqual({
      name: "leave_blank.0.value",
      message: "Control characters are not allowed.",
    });
  });

  it.each([
    "data.surprise: Extra inputs are not permitted",
    "data.employers.9.start: out of range item",
    "data.employers: duplicate employer key",
    "data.employers.0: something else entirely",
    "body.data: Input should be a valid dictionary",
    "base_revision: Input should be greater than or equal to 0",
    "not a detail at all",
    "",
  ])("gives undefined for %j", (detail) => {
    expect(serverFieldError(detail, experience)).toBeUndefined();
  });

  it("keeps a message that already ends with a full stop", () => {
    expect(
      serverFieldError("data.phone: must be a phone number.", identity)
        ?.message,
    ).toBe("Must be a phone number.");
  });
});

describe("validationDetail", () => {
  it("reads the detail of a 422 only", () => {
    const e422 = new ApiError("http", "x", {
      status: 422,
      body: { detail: "data.email: bad" },
    });
    const e409 = new ApiError("http", "x", {
      status: 409,
      body: { detail: "stale" },
    });
    expect(validationDetail(e422)).toBe("data.email: bad");
    expect(validationDetail(e409)).toBeUndefined();
    expect(validationDetail(new Error("x"))).toBeUndefined();
  });
});
