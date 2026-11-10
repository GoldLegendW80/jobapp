import { beforeEach, describe, expect, it } from "vitest";

import { FAILED_RUN, SWEEP_RUN } from "@/design-system/fixtures/activity";

import {
  ANNOUNCED_KEY,
  ANNOUNCED_MAX,
  claimRun,
  firstSight,
  newlyEnded,
  releaseRun,
  runEndWords,
} from "../helpers/run-ends";

const at = (id: string, finished_at: string) => ({ id, finished_at });

beforeEach(() => {
  window.localStorage.clear();
});

describe("newlyEnded", () => {
  it("keeps the runs that ended after the first count, and none it saw", () => {
    const baseline = firstSight([
      at("b", "2026-11-10T08:30:00Z"),
      at("a", "2026-11-10T08:00:00Z"),
    ]);
    expect(baseline.mark).toBe(Date.parse("2026-11-10T08:30:00Z"));
    expect(
      newlyEnded(baseline, [
        at("c", "2026-11-10T09:00:00Z"),
        at("b", "2026-11-10T08:30:00Z"),
        at("old", "2026-11-10T07:00:00Z"),
        at("same", "2026-11-10T08:30:00Z"),
        at("unreadable", "not a time"),
      ]).map((run) => run.id),
    ).toEqual(["c"]);
  });

  it("keeps every run when none had ended at the first count", () => {
    const baseline = firstSight([]);
    expect(baseline.mark).toBeNull();
    expect(
      newlyEnded(baseline, [at("a", "2026-11-10T08:00:00Z")]).map(
        (run) => run.id,
      ),
    ).toEqual(["a"]);
  });
});

describe("claimRun", () => {
  it("lets one page of the browser announce a run, once", () => {
    expect(claimRun("a")).toBe(true);
    expect(claimRun("a")).toBe(false);
    releaseRun("a");
    expect(claimRun("a")).toBe(true);
  });

  it("forgets the oldest runs past its bound", () => {
    for (let n = 0; n <= ANNOUNCED_MAX; n += 1) claimRun(`run-${String(n)}`);
    const kept = JSON.parse(
      window.localStorage.getItem(ANNOUNCED_KEY) ?? "[]",
    ) as string[];
    expect(kept).toHaveLength(ANNOUNCED_MAX);
    expect(kept[0]).toBe("run-1");
    expect(claimRun(`run-${String(ANNOUNCED_MAX)}`)).toBe(false);
  });

  it("reads a damaged list as empty, and announces when storage is blocked", () => {
    window.localStorage.setItem(ANNOUNCED_KEY, "{not json");
    expect(claimRun("a")).toBe(true);
    expect(claimRun("a")).toBe(false);
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(claimRun("b", blocked)).toBe(true);
    expect(claimRun("b", null)).toBe(true);
  });
});

describe("runEndWords", () => {
  it("names a search's workflow, its subject and its new postings", () => {
    expect(runEndWords(SWEEP_RUN)).toEqual({
      title: "Job search: Succeeded",
      lines: [
        "Data engineer, Analytics engineer in Lyon, Remote.",
        "12 new postings added.",
      ],
      postings: 12,
    });
    expect(runEndWords({ ...SWEEP_RUN, new_postings: 1 }).lines[1]).toBe(
      "1 new posting added.",
    );
    expect(runEndWords({ ...SWEEP_RUN, new_postings: 0 })).toMatchObject({
      lines: [expect.any(String), "No new posting added."],
      postings: 0,
    });
  });

  it("says why a run failed, or that a step did when the api says no more", () => {
    expect(runEndWords(FAILED_RUN)).toEqual({
      title: "Application: Failed",
      lines: [
        "Platform engineer at Lumen Freight.",
        "Answer writer: The run reached its limit of 40 turns.",
      ],
      postings: 0,
    });
    expect(
      runEndWords({ ...FAILED_RUN, subject: null, reason: null }).lines,
    ).toEqual(["A step of this run failed."]);
  });

  it("says a cancelled run was cancelled, with no reason", () => {
    expect(
      runEndWords({ ...FAILED_RUN, status: "cancelled", subject: null }),
    ).toEqual({ title: "Application: Cancelled", lines: [], postings: 0 });
  });
});
