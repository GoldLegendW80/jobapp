/** Jobs J27: the app continuing an agent's session, and the run that still ended with its plan
 * left, in the step log. */
import { describe, expect, it } from "vitest";

import type { RunStep } from "../api/runs-api";
import { describeStep } from "../helpers/run-steps";

const step = (kind: string, data: Record<string, unknown>): RunStep => ({
  seq: 1,
  at: "2026-11-19T10:00:00Z",
  kind,
  data,
});

describe("a follow up in the step log", () => {
  it("says the app asked the agent to go on, and which time", () => {
    const view = describeStep(step("follow_up", { attempt: 1, of: 2 }), null);
    expect(view?.title).toBe("Asked to go on (1 of 2)");
    expect(view?.tone).toBe("muted");
  });

  it("says the run ended with its plan unfinished, what it found kept", () => {
    const view = describeStep(step("unfinished", { follow_ups: 2 }), null);
    expect(view?.title).toBe("Ended with its plan unfinished");
    expect(view?.tone).toBe("warning");
    expect(view?.body).toEqual({
      kind: "note",
      text: "The assistant ended its turn again after the app asked it to go on. What it found is kept.",
    });
  });

  it("writes no dash as a separator", () => {
    for (const kind of ["follow_up", "unfinished"]) {
      const view = describeStep(step(kind, { attempt: 2, of: 2 }), null);
      const body = view?.body && "text" in view.body ? view.body.text : "";
      expect(`${view?.title} ${body}`).not.toMatch(/[–—]| - /);
    }
  });
});
