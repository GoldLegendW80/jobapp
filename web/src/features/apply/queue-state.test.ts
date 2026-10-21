import { describe, expect, it } from "vitest";

import { queueItem } from "@/test/apply-queue";

import { serverOffset } from "./full-auto-state";
import {
  canCancel,
  canReject,
  checkLine,
  isWaiting,
  leftLine,
  needsPostingPage,
  QUEUE_POLL_MS,
  QUEUE_WAIT_POLL_MS,
  queuePoll,
  reasonLine,
} from "./queue-state";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

describe("the line of a document and its check", () => {
  it.each([
    ["cv", 3, "one_page", 0, "CV version 3: fits on one page"],
    ["letter", 2, "overflow", 0, "Letter version 2: runs over one page"],
    ["cv", 1, null, 0, "CV version 1: page not checked yet"],
    ["cv", 1, "rendering", 0, "CV version 1: page not checked yet"],
    [
      "cv",
      4,
      "one_page",
      1,
      "CV version 4: fits on one page, 1 correction to look at",
    ],
    [
      "letter",
      5,
      "overflow",
      3,
      "Letter version 5: runs over one page, 3 corrections to look at",
    ],
    ["cv", null, "one_page", 0, "CV: fits on one page"],
    ["portfolio", 2, "one_page", 0, "Portfolio version 2: fits on one page"],
  ] as const)("%s %s %s %s", (kind, number, fit, corrections, line) => {
    expect(checkLine(kind, { number, fit, corrections })).toBe(line);
    expect(line).not.toMatch(SEPARATOR);
  });
});

describe("what is left", () => {
  it.each([
    [0, 0, null],
    [1, 1, "1 field left for you, required."],
    [1, 0, "1 field left for you, optional."],
    [3, 0, "3 fields left for you, all optional."],
    [3, 1, "3 fields left for you, 1 of them required."],
    [3, 3, "3 fields left for you, 3 of them required."],
  ] as const)("%s left, %s required", (left, required, line) => {
    expect(
      leftLine(queueItem(1, "ready", { left, required_left: required })),
    ).toBe(line);
  });
});

describe("why an item came back", () => {
  it("shows the api's sentence, else the code in words", () => {
    expect(reasonLine(null)).toBeNull();
    expect(reasonLine({ code: "stale", sentence: "The form changed." })).toBe(
      "The form changed.",
    );
    expect(reasonLine({ code: "form_changed", sentence: null })).toBe(
      "Form changed.",
    );
  });
});

describe("the actions of each state", () => {
  it.each([
    ["ready", true, true, false, false],
    ["needs_person", true, true, false, true],
    ["refused_by_site", true, false, false, true],
    ["submit_unknown", true, false, false, true],
    ["queued", false, false, true, false],
    ["filling", false, false, true, false],
    ["approved", false, false, true, false],
    ["held", false, false, true, false],
    ["submitting", false, false, false, false],
    ["a_state_from_later", false, false, false, false],
  ] as const)("%s", (state, waiting, reject, cancel, page) => {
    const item = queueItem(1, state);
    expect(isWaiting(item)).toBe(waiting);
    expect(canReject(item)).toBe(reject);
    expect(canCancel(item)).toBe(cancel);
    expect(needsPostingPage(item)).toBe(page);
  });
});

describe("how often the queue is read again", () => {
  const now = Date.parse("2026-10-14T09:00:00Z");

  it("polls only while something is under way", () => {
    expect(queuePoll(undefined, now)).toBe(false);
    expect(queuePoll({ items: [], waiting_on_you: 0 }, now)).toBe(false);
    expect(
      queuePoll(
        {
          items: [queueItem(1, "ready"), queueItem(2, "needs_person")],
          waiting_on_you: 2,
        },
        now,
      ),
    ).toBe(false);
    expect(
      queuePoll(
        {
          items: [queueItem(1, "ready"), queueItem(2, "submitting")],
          waiting_on_you: 1,
        },
        now,
      ),
    ).toBe(QUEUE_POLL_MS);
  });

  it("refreshes at the earliest hold expiry on the server's clock", () => {
    const queue = {
      now: "2001-02-03T10:00:00Z",
      items: [
        queueItem(1, "held", { hold_until: "2001-02-03T10:10:00Z" }),
        queueItem(2, "held", { hold_until: "2001-02-03T10:05:00Z" }),
        queueItem(3, "ready"),
      ],
      waiting_on_you: 1,
    };
    const offset = serverOffset(queue.now, now);

    expect(queuePoll(queue, now, offset)).toBe(300_000);
    expect(queuePoll(queue, now + 299_000, offset)).toBe(1_000);
    expect(queuePoll(queue, now + 300_000, offset)).toBe(QUEUE_POLL_MS);
    expect(queuePoll(queue, now + 301_000, offset)).toBe(QUEUE_POLL_MS);
  });

  it.each([undefined, null, "not a date"])(
    "keeps checking a hold with no usable expiry (%s) at a slower rate",
    (hold_until) => {
      expect(
        queuePoll(
          {
            items: [queueItem(1, "held", { hold_until })],
            waiting_on_you: 0,
          },
          now,
        ),
      ).toBe(QUEUE_WAIT_POLL_MS);
    },
  );

  it("keeps checking paced approvals at a slower rate because the api exposes no expiry", () => {
    expect(
      queuePoll(
        {
          items: [queueItem(1, "approved", { needs: "host_pace" })],
          waiting_on_you: 0,
        },
        now,
      ),
    ).toBe(QUEUE_WAIT_POLL_MS);
  });

  it.each([
    ["2026-10-14T09:01:00Z", QUEUE_POLL_MS],
    ["2026-10-14T09:00:01Z", 1_000],
  ] as const)(
    "uses the earliest refresh when active work and a hold coexist",
    (hold_until, interval) => {
      expect(
        queuePoll(
          {
            items: [
              queueItem(1, "held", { hold_until }),
              queueItem(2, "submitting"),
            ],
            waiting_on_you: 0,
          },
          now,
        ),
      ).toBe(interval);
    },
  );

  it.each([
    ["2026-10-14T09:01:00Z", QUEUE_WAIT_POLL_MS],
    ["2026-10-14T09:00:01Z", 1_000],
  ] as const)(
    "uses the earlier of a hold expiry and the pace fallback",
    (hold_until, interval) => {
      expect(
        queuePoll(
          {
            items: [
              queueItem(1, "held", { hold_until }),
              queueItem(2, "approved", { needs: "host_pace" }),
            ],
            waiting_on_you: 0,
          },
          now,
        ),
      ).toBe(interval);
    },
  );
});
