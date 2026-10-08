import { describe, expect, it } from "vitest";

import {
  cleanName,
  nameProblem,
  PASSWORD_HINT,
  passwordLength,
  passwordProblem,
} from "./password-rules";

const email = "Zoe@Example.org";

describe("passwordProblem mirrors the api rules", () => {
  it("asks for a password when empty", () => {
    expect(passwordProblem("", email)).toBe("Choose a password.");
  });

  it("refuses 9 characters and accepts 10", () => {
    expect(passwordProblem("a".repeat(9), email)).toBe(
      "Use at least 10 characters for the password.",
    );
    expect(passwordProblem("correct ho", email)).toBeUndefined();
  });

  it("accepts 128 characters and refuses 129", () => {
    expect(passwordProblem("p".repeat(128), email)).toBeUndefined();
    expect(passwordProblem("p".repeat(129), email)).toBe(
      "Use at most 128 characters for the password.",
    );
  });

  it("counts code points, not UTF-16 units, so emoji count once", () => {
    expect(passwordLength("🔑".repeat(10))).toBe(10);
    expect(passwordProblem("🔑".repeat(10), email)).toBeUndefined();
    expect(passwordProblem("🔑".repeat(9), email)).toMatch(/at least 10/);
  });

  it("counts after NFKC like the api", () => {
    // U+FB01 (fi ligature) becomes two letters under NFKC.
    expect(passwordLength("ﬁ".repeat(5))).toBe(10);
  });

  it("refuses the email address in any case and with spaces around", () => {
    expect(passwordProblem("zoe@example.org", email)).toBe(
      "The password cannot be your email address.",
    );
    expect(passwordProblem(" ZOE@EXAMPLE.ORG ", email)).toBe(
      "The password cannot be your email address.",
    );
  });

  it("leaves common passwords to the api", () => {
    expect(passwordProblem("password12", email)).toBeUndefined();
  });

  it("states the rules in the hint without dashes", () => {
    expect(PASSWORD_HINT).toBe(
      "Use 10 to 128 characters. It cannot be your email address or a common password.",
    );
    expect(PASSWORD_HINT).not.toMatch(/[–—]| - /);
  });
});

describe("names", () => {
  it("collapses whitespace like the api", () => {
    expect(cleanName("  Zoë \t  Ångström \n")).toBe("Zoë Ångström");
  });

  it("asks for a name when blank", () => {
    expect(nameProblem("")).toBe("Enter your name.");
    expect(nameProblem("   \t")).toBe("Enter your name.");
  });

  it("accepts 100 characters and refuses 101", () => {
    expect(nameProblem("é".repeat(100))).toBeUndefined();
    expect(nameProblem("é".repeat(101))).toBe(
      "Use at most 100 characters for the name.",
    );
  });

  it("accepts unicode names", () => {
    expect(nameProblem("李小龍")).toBeUndefined();
    expect(nameProblem("O'Brien-Smith")).toBeUndefined();
  });
});
