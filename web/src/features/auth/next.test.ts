import { describe, expect, it } from "vitest";

import { loginPath, safeNext } from "./next";

describe("safeNext", () => {
  it.each([
    ["/", "/"],
    ["/admin/users", "/admin/users"],
    ["/jobs?status=found&q=café", "/jobs?status=found&q=caf%C3%A9"],
    ["/jobs#top", "/jobs#top"],
    ["/a/../admin/users", "/admin/users"],
    ["/%2F/evil.com", "/%2F/evil.com"],
  ])("keeps the same origin path %j", (raw, expected) => {
    expect(safeNext(raw)).toBe(expected);
  });

  it.each([
    [null],
    [undefined],
    [""],
    ["//evil.com"],
    ["//evil.com/path"],
    ["///evil.com"],
    ["https://evil.com"],
    ["http://evil.com/"],
    ["HTTPS://evil.com"],
    ["/\\evil.com"],
    ["\\\\evil.com"],
    ["/\\/evil.com"],
    ["javascript:alert(1)"],
    ["JaVaScRiPt:alert(1)"],
    [" javascript:alert(1)"],
    ["data:text/html,<script>alert(1)</script>"],
    ["evil.com"],
    ["admin/users"],
    ["/\t/evil.com"],
    ["/\n/evil.com"],
    ["/\r\n/evil.com"],
    ["/\u0000/evil.com"],
    // Encoded forms: the router decodes the query once, a second decoding never happens.
    ["%2F%2Fevil.com"],
    ["%2f%2fevil.com"],
    ["%252F%252Fevil.com"],
    ["%5C%5Cevil.com"],
    ["%09/evil.com"],
    ["/login"],
    ["/login?next=//evil.com"],
  ])("falls back to / for %j", (raw) => {
    expect(safeNext(raw)).toBe("/");
  });

  it("decodes the query parameter once only, so an encoded // stays harmless", () => {
    const params = new URLSearchParams("next=%2F%2Fevil.com");
    expect(params.get("next")).toBe("//evil.com");
    expect(safeNext(params.get("next"))).toBe("/");
    const twice = new URLSearchParams("next=%252F%252Fevil.com");
    expect(safeNext(twice.get("next"))).toBe("/");
  });

  it("keeps a very long path", () => {
    const long = `/jobs/${"x".repeat(5000)}`;
    expect(safeNext(long)).toBe(long);
  });
});

describe("loginPath", () => {
  it("carries the page with readable slashes", () => {
    expect(loginPath({ pathname: "/" })).toBe("/login?next=/");
    expect(loginPath({ pathname: "/admin/users" })).toBe(
      "/login?next=/admin/users",
    );
  });

  it("encodes the search and hash so they survive as one parameter", () => {
    const path = loginPath({
      pathname: "/jobs",
      search: "?a=1&b=2",
      hash: "#x",
    });
    expect(path).toBe("/login?next=/jobs%3Fa%3D1%26b%3D2%23x");
    const next = new URLSearchParams(path.split("?")[1]).get("next");
    expect(next).toBe("/jobs?a=1&b=2#x");
    expect(safeNext(next)).toBe("/jobs?a=1&b=2#x");
  });

  it("never points the sign in page at itself", () => {
    expect(loginPath({ pathname: "/login", search: "?next=/x" })).toBe(
      "/login",
    );
  });
});
