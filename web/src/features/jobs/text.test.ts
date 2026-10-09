import { describe, expect, it } from "vitest";

import { fitTone, statusLabel, STATUS_LABELS, STATUSES } from "./labels";
import { describeEvent, paragraphs, prettyJson, safeHttpUrl } from "./text";

describe("safeHttpUrl", () => {
  it.each([
    ["https://jobs.ashbyhq.com/acme/1", "https://jobs.ashbyhq.com/acme/1"],
    ["http://example.org", "http://example.org/"],
    ["  https://example.org/a b  ", null],
    ["javascript:alert(1)", null],
    ["JaVaScRiPt:alert(1)", null],
    ["java\tscript:alert(1)", null],
    ["data:text/html,<script>alert(1)</script>", null],
    ["vbscript:msgbox(1)", null],
    ["file:///etc/passwd", null],
    ["//evil.example/path", null],
    ["/relative/path", null],
    ["not a url", null],
    ["", null],
    [null, null],
  ])("%s gives %s", (raw, expected) => {
    expect(safeHttpUrl(raw)).toBe(expected);
  });
});

describe("paragraphs", () => {
  it("splits on blank lines and keeps single newlines inside", () => {
    expect(paragraphs("One\nstill one\n\nTwo\r\n\r\n\r\nThree")).toEqual([
      "One\nstill one",
      "Two",
      "Three",
    ]);
  });

  it("treats lines of spaces as blank and drops empty parts", () => {
    expect(paragraphs("\n\nA\n   \nB\n\n\n")).toEqual(["A", "B"]);
    expect(paragraphs("   ")).toEqual([]);
  });

  it("keeps markup as plain text", () => {
    expect(paragraphs("<script>x</script>\n\n&amp;")).toEqual([
      "<script>x</script>",
      "&amp;",
    ]);
  });
});

describe("labels", () => {
  it("has a human label for every status, CV ready included", () => {
    expect(STATUSES.map((s) => STATUS_LABELS[s])).toEqual([
      "Found",
      "Shortlisted",
      "CV ready",
      "Staged",
      "Applied",
      "Interview",
      "Rejected",
      "Closed",
      "Skipped",
    ]);
  });

  it.each([
    ["on_hold", "On hold"],
    ["offer", "Offer"],
    ["__", "Unknown"],
    ["", "Unknown"],
  ])("falls back for the unknown status %s", (status, label) => {
    expect(statusLabel(status)).toBe(label);
  });

  it.each([
    [null, "none"],
    [0, "low"],
    [49, "low"],
    [50, "fair"],
    [69, "fair"],
    [70, "good"],
    [84, "good"],
    [85, "top"],
    [100, "top"],
  ] as const)("fit %s is %s", (fit, tone) => {
    expect(fitTone(fit)).toBe(tone);
  });
});

describe("describeEvent", () => {
  it.each([
    ["created", { via: "import" }, "Imported"],
    ["created", {}, "Added"],
    [
      "status",
      { from: "found", to: "cv_ready" },
      "Status changed from Found to CV ready",
    ],
    ["fit", { from: null, to: 80 }, "Fit changed from none to 80"],
    [
      "status",
      { from: "found", to: "skipped", via: "bulk" },
      "Status changed from Found to Skipped (bulk action)",
    ],
    [
      "status",
      { from: "skipped", to: "found", via: "undo" },
      "Status changed from Skipped to Found (undone)",
    ],
    [
      "fit",
      { from: 70, to: 42, manual: true },
      "Fit set by hand from 70 to 42",
    ],
    [
      "fit",
      { from: 88, to: 88, manual: true, via: "mark-overrides" },
      "Fit 88 marked as set by hand",
    ],
    [
      "fit",
      { from: 42, to: 70, manual: false, was_manual: true },
      "Fit back to the computed 70, was 42 set by hand",
    ],
    [
      "fit",
      { from: 60, to: 70, manual: false, via: "rescore" },
      "Fit changed from 60 to 70 (rescore)",
    ],
    ["note", { from: "", to: "x" }, "Notes edited"],
    ["note", { from: "x", to: " " }, "Notes cleared"],
    ["note", { from: "", to: "x", via: "bulk" }, "Note added (bulk action)"],
    [
      "edit",
      { changes: { company: {}, contract_type: {}, url: {} } },
      "Edited the company, contract type, link",
    ],
    ["edit", {}, "Edited"],
    ["import", { changed: ["notes"] }, "Updated by an import"],
    ["brand_new", {}, "Brand new"],
  ])("%s %j", (kind, data, text) => {
    expect(describeEvent(kind, data)).toBe(text);
  });
});

describe("prettyJson", () => {
  it("indents objects and reparses strings", () => {
    expect(prettyJson({ a: [1] })).toBe('{\n  "a": [\n    1\n  ]\n}');
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyJson("not json")).toBe("not json");
    expect(prettyJson(undefined)).toBe("");
  });
});
