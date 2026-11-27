import { describe, expect, it } from "vitest";

import { fakeAnswer, fakeApplication, leftAnswer } from "@/test/apply";

import { cancelKindOf } from "../helpers/apply-section-rules";
import {
  blockingFields,
  editorKind,
  eventLine,
  hasForm,
  hasReport,
  isEditable,
  isOpenField,
  phaseOf,
  pollInterval,
  reviewReason,
  statusLine,
  valueText,
} from "../helpers/apply-state";

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
    expect(statusLine(null)).toBe("No application yet.");
  });

  it("says a queued fill waits for the person's own sign in, not for a free browser", () => {
    const line = statusLine(
      fakeApplication("queued", { needs: "browser_signin" }),
    );
    expect(line).toBe(
      "Waiting for you to finish signing in to a site in your browser. The form is filled after that.",
    );
    expect(line).not.toMatch(SEPARATOR);
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

describe("an application the old document check stopped (A22)", () => {
  it.each([
    ["resend_expired", "more than 7 days old"],
    ["resend_changed", "changed since your Like"],
    ["resend_full_auto_off", "Full auto was turned off"],
    ["resend_used", "already used"],
  ])("says why %s waits for the approval", (code, why) => {
    const said = reviewReason(
      fakeApplication("ready", { mode: "review", error_code: code }),
    );
    expect(said).toContain(
      "The document check that stopped this application is gone",
    );
    expect(said).toContain(why);
    expect(said).not.toMatch(SEPARATOR);
  });

  it("says why Full auto left a posting outside the search's criteria (J30)", () => {
    const said = reviewReason(
      fakeApplication("ready", {
        mode: "review",
        error_code: "outside_criteria",
      }),
    );
    expect(said).toBe(
      "This posting is outside the criteria of your search, so it waits for you.",
    );
  });

  it.each([
    ["back_to_auto", "Back to sending by itself"],
    ["kept_for_approval", "Kept for your approval"],
  ])("names the %s step of the history", (kind, line) => {
    const said = eventLine({ at: "2026-11-10T09:00:00Z", kind, data: {} });
    expect(said).toContain(line);
    expect(said).not.toMatch(SEPARATOR);
  });
});

describe("the person's Cancel at any moment (A24)", () => {
  it.each([
    ["cancelled", "cancelled_by_person", "Cancelled by you. Nothing was sent."],
    [
      "submit_unknown",
      "cancelled_by_person",
      "You stopped it after a send button had been clicked. Check the site or your mail.",
    ],
    [
      "cancelled",
      "not_sent",
      "You said it was not sent, so it ended. The app does not send it again by itself: apply again if you want to.",
    ],
    [
      "submit_unknown",
      "no_confirmation",
      "We could not confirm this application. Look for a confirmation mail or on the site.",
    ],
    // A25: a page showed the code the person gave again, so browserd closed the tools.
    [
      "failed",
      "code_shown",
      "The last attempt failed: the code you gave showed again, so the assistant stopped. Nothing was sent.",
    ],
    [
      "submit_unknown",
      "code_shown",
      "The code you gave showed again, so the assistant stopped, and the site may already have the application. Look for a confirmation mail or on the site.",
    ],
    [
      "cancelled",
      "rejected",
      "The last application was cancelled. Nothing was sent.",
    ],
    [
      "submitted",
      "sent_before_cancel",
      "Applied: your Cancel came after the site had already confirmed it.",
    ],
  ])("%s with %s says what is true", (state, code, line) => {
    for (const mode of ["review", "full_auto", "direct"]) {
      const said = statusLine(
        fakeApplication(state, { mode, error_code: code }),
      );
      expect(said).toBe(line);
      expect(said).not.toMatch(SEPARATOR);
    }
  });

  it.each([
    ["queued", "cancel"],
    ["filling", "cancel"],
    ["needs_person", "cancel"],
    ["ready", "cancel"],
    ["approved", "cancel"],
    ["held", "cancel"],
    ["refused_by_site", "cancel"],
    ["submitting", "cancelAfterSend"],
    ["submit_unknown", "notSent"],
    ["submitted", null],
    ["cancelled", null],
    ["failed", null],
    ["blocked", null],
    ["posting_closed", null],
    ["rehearsed", null],
    ["a_state_from_later", null],
  ])("offers in %s: %s, in every mode but Apply myself", (state, kind) => {
    for (const mode of ["review", "full_auto", "direct"]) {
      expect(cancelKindOf(fakeApplication(state, { mode }))).toBe(kind);
    }
    expect(cancelKindOf(fakeApplication(state, { mode: "manual" }))).toBe(null);
  });

  it("leaves an e-mail application being sent to the mail's own end", () => {
    for (const state of ["submitting", "submit_unknown"]) {
      expect(cancelKindOf(fakeApplication(state, { ats: "email" }))).toBeNull();
    }
    expect(cancelKindOf(fakeApplication("ready", { ats: "email" }))).toBe(
      "cancel",
    );
  });
});
