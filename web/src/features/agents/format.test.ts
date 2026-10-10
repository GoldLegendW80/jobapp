import { describe, expect, it } from "vitest";

import {
  costLabel,
  costNote,
  costQualifier,
  describeCost,
  formatCost,
} from "./format";

/**
 * Issue #33: the SDK reports an amount for every run. It is a cost with an API key, an estimate
 * that nobody is billed with a subscription, and worded neutrally when the kind of credential
 * was not recorded.
 */
describe("the amount of a run, by the kind of credential it used", () => {
  it("is the cost with an Anthropic API key", () => {
    expect(costLabel("anthropic_api_key")).toBe("Cost");
    expect(costQualifier("anthropic_api_key")).toBeNull();
    expect(costNote("anthropic_api_key")).toBeNull();
    expect(describeCost(0.193, "anthropic_api_key")).toBe("$0.193");
  });

  it("is an estimate at API prices, not billed, with a Claude subscription", () => {
    expect(costLabel("claude_subscription")).toBe("Estimate at API prices");
    expect(costQualifier("claude_subscription")).toBe("Estimate, not billed");
    expect(costNote("claude_subscription")).toBe(
      "This run used your Claude subscription, which is not billed per run. The estimate is what the same tokens cost at API prices.",
    );
    expect(describeCost(0.193, "claude_subscription")).toBe(
      "$0.193 estimated at API prices, not billed",
    );
  });

  it.each([null, undefined])(
    "is worded neutrally when the kind is %s (a run from before it was recorded)",
    (kind) => {
      expect(costLabel(kind)).toBe("Amount at API prices");
      expect(costQualifier(kind)).toBe("At API prices");
      expect(costNote(kind)).toBe(
        "The kind of credential this run used was not recorded, so whether this amount was billed is not known.",
      );
      expect(describeCost(0.193, kind)).toBe("$0.193 at API prices");
      // Neither "cost" nor "billed" nor "estimate" is claimed for it.
      for (const text of [
        costLabel(kind),
        costQualifier(kind) ?? "",
        describeCost(0.193, kind),
      ]) {
        expect(text).not.toMatch(/\bcost\b|not billed|estimate/i);
      }
    },
  );

  it("says nothing more than the absence of an amount when there is none", () => {
    for (const kind of [
      "anthropic_api_key",
      "claude_subscription",
      null,
    ] as const) {
      expect(describeCost(null, kind)).toBe(formatCost(null));
      expect(describeCost(undefined, kind)).toBe("None yet");
    }
  });

  it("uses no dash as a separator in any wording", () => {
    for (const kind of [
      "anthropic_api_key",
      "claude_subscription",
      null,
    ] as const) {
      for (const text of [
        costLabel(kind),
        costQualifier(kind) ?? "",
        costNote(kind) ?? "",
        describeCost(1.5, kind),
      ]) {
        expect(text).not.toMatch(/[–—]| - /);
      }
    }
  });
});
