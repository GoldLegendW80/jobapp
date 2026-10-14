import { describe, expect, it } from "vitest";

import { fakeAnswer, fakeApplication, leftAnswer } from "@/test/apply";

import {
  blockingFields,
  editorKind,
  hasForm,
  hasReport,
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
