import { describe, expect, it } from "vitest";

import { fakeAnswer, fakeApplication, leftAnswer } from "@/test/apply";

import {
  blockingFields,
  editorKind,
  hasForm,
  hasReport,
  isEditable,
  isOpenField,
  phaseOf,
  pollInterval,
  statusLine,
  valueText,
} from "./apply-state";

const SEPARATOR = /[–—]| - /;

describe("phaseOf and what each phase reads", () => {
  it.each([
    ["queued", "waiting", 1500, false, false],
    ["filling", "filling", 1500, false, false],
    ["needs_person", "needs", false, true, false],
    ["ready", "review", false, true, false],
    ["approved", "submitting", 1500, true, false],
    ["held", "submitting", 1500, true, false],
    ["submitting", "submitting", 1500, true, false],
    ["submitted", "submitted", false, true, true],
    ["submit_unknown", "unknown", false, true, true],
    ["refused_by_site", "refused", false, true, true],
    ["cancelled", "idle", false, false, false],
    ["failed", "idle", false, false, false],
    ["blocked", "idle", false, false, false],
    ["posting_closed", "idle", false, false, false],
    ["rehearsed", "idle", false, false, false],
    ["something_new", "other", false, false, false],
  ])("%s is %s", (state, phase, poll, form, report) => {
    const application = fakeApplication(state);
    expect(phaseOf(application)).toBe(phase);
    expect(pollInterval(application)).toBe(poll);
    expect(hasForm(application)).toBe(form);
    expect(hasReport(application)).toBe(report);
    expect(statusLine(application)).not.toMatch(SEPARATOR);
  });

  it("is idle with no application", () => {
    expect(phaseOf(null)).toBe("idle");
    expect(statusLine(null)).toMatch(/Nothing is sent without your approval/);
  });

  it.each([
    ["submit_not_designated", "could not identify the form's Submit button"],
    [
      "submit_not_highlighted",
      "could not show the Submit button for your review",
    ],
  ])("explains %s without internal metadata", (needs, sentence) => {
    expect(statusLine({ ...fakeApplication("needs_person"), needs })).toContain(
      sentence,
    );
  });
});

describe("the fields", () => {
  it("blocks only on a left, required and still empty field", () => {
    const answers = [
      leftAnswer("a", "A", { required: true }),
      leftAnswer("b", "B"),
      fakeAnswer("c", "C", "typed", { you_answer: true, required: true }),
      fakeAnswer("d", "D", "x", { required: true }),
      leftAnswer("e", "E", { required: true, value: false }),
    ];
    expect(blockingFields(answers).map((one) => one.field_key)).toEqual(["a"]);
    expect(answers.map(isOpenField)).toEqual([true, true, false, false, false]);
  });

  it.each([
    ["city", "text", true],
    ["why", "textarea", true],
    ["start", "select", true],
    ["start", "combobox", true],
    ["role", "single", true],
    ["day", "date", true],
    ["ok", "checkbox", true],
    ["ok", "yes_no", true],
    ["resume", "file", false],
    ["tags", "multi", false],
    ["designated:submit", "designated", false],
    ["designated:strategy", "text", false],
    ["designation", "designated", false],
    ["skipped:0:upload", "text", false],
    ["skipped:", "text", false],
    ["notskipped", "text", true],
    ["a:skipped:b", "text", true],
    ["Skipped:0", "text", true],
    ["", "text", true],
    ["x", "something_new", true],
  ])(
    "isEditable(%s, %s) is %s, as the api's set_answer takes it",
    (key, kind, wanted) => {
      expect(isEditable(leftAnswer(key, "L", { kind }))).toBe(wanted);
    },
  );

  it("never blocks on a left required field that cannot be typed here", () => {
    const answers = [
      leftAnswer("skipped:0:x", "S", { required: true }),
      leftAnswer("f", "F", { kind: "file", required: true }),
      leftAnswer("m", "M", { kind: "multi", required: true }),
      leftAnswer("t", "T", { required: true }),
    ];
    expect(blockingFields(answers).map((one) => one.field_key)).toEqual(["t"]);
  });

  it.each([
    [{ kind: "yes_no", value: null }, "yes_no"],
    [{ kind: "text", value: true }, "yes_no"],
    [{ kind: "select", options: ["a"] }, "select"],
    [{ kind: "text", options: [] }, "text"],
    [{ kind: "textarea" }, "long"],
    [{ kind: "text", value: "x".repeat(81) }, "long"],
    [{ kind: "text", value: "short" }, "text"],
  ])("types %j with %s", (fields, kind) => {
    expect(editorKind({ ...leftAnswer("k", "K"), ...fields })).toBe(kind);
  });

  it.each([
    [null, ""],
    [undefined, ""],
    ["", ""],
    [true, "Yes"],
    [false, "No"],
    [3, "3"],
    [["a", "", "b"], "a, b"],
    ["text", "text"],
  ])("reads %j as %j", (value, text) => {
    expect(valueText(value)).toBe(text);
  });
});
