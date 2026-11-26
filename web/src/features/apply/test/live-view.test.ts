import { describe, expect, it } from "vitest";

import { fakeApplication } from "@/test/apply";

import { liveStatus, liveWait } from "../helpers/live-view";

const SEPARATOR = /[–—]| - /;
const PACE = /spaces out/;
const STATES = ["queued", "filling", "approved", "held", "submitting"];
const PURPOSES = [
  "apply_fill",
  "full_auto",
  "apply_agent",
  "apply_review",
  "apply_submit",
];

describe("the status line over the lease", () => {
  it.each([
    [
      "apply_agent",
      "filling",
      "The assistant is filling the form. It sends it by itself once it is filled.",
    ],
    [
      "apply_agent",
      "submitting",
      "The assistant is sending the form. It may still fill a step or two first.",
    ],
    [
      "apply_review",
      "filling",
      "The assistant is filling the form. Then you check what it filled and confirm.",
    ],
    [
      "apply_review",
      "approved",
      "The app sends the form you confirmed. The assistant then reads the site's answer.",
    ],
    [
      "apply_review",
      "submitting",
      "The app sends the form you confirmed. The assistant then reads the site's answer.",
    ],
    ["apply_fill", "filling", "The assistant is filling the form."],
    ["full_auto", "filling", "The assistant is filling the form."],
  ])("says what a lease of %s does while %s", (purpose, state, sentence) => {
    expect(
      liveStatus(purpose, fakeApplication(state, { mode: "direct" })),
    ).toBe(sentence);
  });

  it.each([
    [
      "review",
      false,
      "The assistant fills the form again with your approved answers, then submits it.",
    ],
    [
      "full_auto",
      true,
      "A rehearsal: the assistant fills the form again with your approved answers, then stops without submitting it.",
    ],
    [
      "direct",
      false,
      "The assistant fills the form again with the same answers, then sends it.",
    ],
  ])(
    "says what the submit lease of a %s application does (rehearsal %s)",
    (mode, dryRun, sentence) => {
      for (const state of ["approved", "held", "submitting"]) {
        expect(
          liveStatus(
            "apply_submit",
            fakeApplication(state, { mode, dry_run: dryRun }),
          ),
        ).toBe(sentence);
      }
    },
  );

  it("uses no dash as a separator", () => {
    for (const purpose of PURPOSES)
      for (const state of STATES)
        expect(liveStatus(purpose, fakeApplication(state))).not.toMatch(
          SEPARATOR,
        );
  });
});

describe("what an application without its lease waits for", () => {
  it.each([
    [
      "host_pace",
      "Waiting for its turn. The app spaces out submissions to the same site, so this one waits a little.",
    ],
    [
      "browser_queue",
      "Waiting for the browser, which is busy with another task. The live view shows here once it is free.",
    ],
    [
      "browser_unavailable",
      "The browser cannot be reached just now. The app tries again by itself.",
    ],
    [
      "browser_signin",
      "Waiting for you to finish signing in to a site in your browser. The form is filled after that.",
    ],
    [
      "retry",
      "The last attempt did not finish. The assistant tries again by itself.",
    ],
    [
      "documents:writing",
      "The browser starts once your CV and letter are ready.",
    ],
    [
      "documents:credential",
      "The browser starts once your CV and letter are ready.",
    ],
  ])("reads %s from its needs, whatever its state", (needs, sentence) => {
    for (const state of STATES) {
      expect(liveWait(fakeApplication(state, { needs }))).toBe(sentence);
    }
  });

  it.each([
    [
      "queued",
      "The browser starts soon. Then you can watch the assistant fill the form here.",
    ],
    ["filling", "Your browser is starting."],
    [
      "approved",
      "Your browser starts soon. Then you can watch the assistant send the form here.",
    ],
    [
      "held",
      "After the hold, you can watch the assistant fill the form again here.",
    ],
    [
      "submitting",
      "Sending the form. Its result shows here once the app has it.",
    ],
  ])("says what comes while %s with no needs", (state, sentence) => {
    expect(liveWait(fakeApplication(state))).toBe(sentence);
  });

  it.each([null, "toString", "constructor", "an_unknown_need"])(
    "never says submissions are spaced out unless a pace holds it (%s)",
    (needs) => {
      for (const state of [...STATES, "an_unknown_state", "valueOf"]) {
        const sentence = liveWait(fakeApplication(state, { needs }));
        expect(typeof sentence).toBe("string");
        expect(sentence).not.toMatch(PACE);
        expect(sentence).not.toMatch(SEPARATOR);
      }
    },
  );
});
