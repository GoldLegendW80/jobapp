/** Agents A13: a run that waits, refused as busy or waiting its turn, in the step log. */
import { describe, expect, it } from "vitest";

import type { RunStep } from "../api/runs-api";
import { describeStep } from "../helpers/run-steps";

const step = (data: Record<string, unknown>): RunStep => ({
  seq: 4,
  at: "2026-11-28T10:00:00Z",
  kind: "waiting",
  data,
});

const DASHES = /[\u2013\u2014]| - /;

describe("a run that waits in the step log", () => {
  it("says Anthropic is busy, when the app tries again and which try", () => {
    const view = describeStep(
      step({
        reason: "busy",
        at: "2026-11-28T10:00:30Z",
        retry: "refused",
        try: 2,
        of: 4,
      }),
      null,
    );
    expect(view?.title).toBe("Anthropic is busy");
    expect(view?.tone).toBe("warning");
    const body = view?.body;
    expect(body?.kind).toBe("note");
    const words = body && "text" in body ? body.text : "";
    expect(words).toMatch(
      /^Anthropic is busy or rate limiting your credential\. The app tries again at \d{2}:\d{2} \(try 2 of 4\)\. Cancel stops it now\.$/,
    );
    expect(words).not.toMatch(DASHES);
  });

  it("says a run waits its turn when many runs started in the last minute", () => {
    const view = describeStep(
      step({
        reason: "pace",
        at: "2026-11-28T10:01:00Z",
        retry: "held",
        try: 1,
        of: 4,
      }),
      null,
    );
    expect(view?.title).toBe("Waiting its turn");
    const body = view?.body;
    const words = body && "text" in body ? body.text : "";
    expect(words).toMatch(/starts at \d{2}:\d{2}\. Cancel stops it now\.$/);
    expect(words).not.toMatch(DASHES);
  });

  it("says in a moment when the time cannot be read", () => {
    const view = describeStep(
      step({ reason: "busy", at: "soon", try: 3, of: 4 }),
      null,
    );
    const body = view?.body;
    expect(body && "text" in body ? body.text : "").toContain(
      "tries again in a moment (try 3 of 4)",
    );
  });
});
