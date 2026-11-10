/**
 * The toast of a run's end, no React: which ended runs a page announces, the runs announced
 * already (shared by the tabs of this browser), and the toast's words.
 */
import { plural } from "@/features/jobs/helpers/selection";

import type { EndedRun, WorkflowRunDetail } from "../api/activity-api";

/** What the first count a page read said had ended: those runs ended before the page opened. */
export interface Baseline {
  /** The latest end then, by the server's clock; null when no run had ended. */
  mark: number | null;
  seen: Set<string>;
}

export function firstSight(ended: readonly EndedRun[]): Baseline {
  const ends = ended.map((run) => Date.parse(run.finished_at));
  const known = ends.filter((end) => !Number.isNaN(end));
  return {
    mark: known.length ? Math.max(...known) : null,
    seen: new Set(ended.map((run) => run.id)),
  };
}

/**
 * The runs of a later count that ended after the page opened: not seen at first sight, and an
 * end after the latest one seen then (a run older than the list's first ten never comes in).
 */
export function newlyEnded(
  baseline: Baseline,
  ended: readonly EndedRun[],
): EndedRun[] {
  return ended.filter((run) => {
    if (baseline.seen.has(run.id)) return false;
    const end = Date.parse(run.finished_at);
    if (Number.isNaN(end)) return false;
    return baseline.mark === null || end > baseline.mark;
  });
}

// The runs announced in this browser, the newest last; the oldest are forgotten past the bound.
export const ANNOUNCED_KEY = "activity:announced-runs";
export const ANNOUNCED_MAX = 200;

type Store = Pick<Storage, "getItem" | "setItem">;

function browserStore(): Store | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function announced(store: Store): string[] {
  try {
    const held: unknown = JSON.parse(store.getItem(ANNOUNCED_KEY) ?? "[]");
    return Array.isArray(held)
      ? held.filter((id): id is string => typeof id === "string")
      : [];
  } catch {
    return [];
  }
}

function keep(store: Store, ids: string[]) {
  try {
    store.setItem(ANNOUNCED_KEY, JSON.stringify(ids.slice(-ANNOUNCED_MAX)));
  } catch {
    // Storage full or blocked: the page's own memory still announces each run once.
  }
}

/** True when this page is the first in the browser to announce the run; it is then marked. */
export function claimRun(
  id: string,
  store: Store | null = browserStore(),
): boolean {
  if (!store) return true;
  const ids = announced(store);
  if (ids.includes(id)) return false;
  keep(store, [...ids, id]);
  return true;
}

/** Gives a claim back, when the run's summary could not be read: a later count tries again. */
export function releaseRun(id: string, store: Store | null = browserStore()) {
  if (!store) return;
  keep(
    store,
    announced(store).filter((one) => one !== id),
  );
}

export interface RunEndWords {
  title: string;
  /** The subject, the reason of a failure, a search's count: one sentence each. */
  lines: string[];
  /** The new postings a search added, to link to. */
  postings: number;
}

const END_WORDS: Partial<Record<WorkflowRunDetail["status"], string>> = {
  success: "Succeeded",
  failure: "Failed",
  cancelled: "Cancelled",
};

function sentence(text: string) {
  const trimmed = text.trim();
  return /[.!?…]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

export function runEndWords(run: WorkflowRunDetail): RunEndWords {
  const lines: string[] = [];
  if (run.subject?.words) lines.push(sentence(run.subject.words));
  if (run.status === "failure") {
    lines.push(sentence(run.reason ?? "A step of this run failed"));
  }
  const postings = run.new_postings ?? 0;
  if (run.new_postings !== null) {
    lines.push(
      postings
        ? `${plural(postings, "new posting")} added.`
        : "No new posting added.",
    );
  }
  return {
    title: `${run.workflow_title}: ${END_WORDS[run.status] ?? "Ended"}`,
    lines,
    postings,
  };
}

/** Where a toast leads: the run on Activity, and a search's new postings in the Jobs list. */
export const runHref = (id: string) =>
  `/activity/runs/${encodeURIComponent(id)}`;
export const NEW_POSTINGS_HREF = "/jobs?status=found";
