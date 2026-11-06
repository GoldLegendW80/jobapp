/** The sentences of a run on the page: its headline, what each step does, why a document was not read. */
import { WORDING } from "@/lib/wording";

import {
  PAUSED_ON_CEILING,
  tokens,
  type Run,
  type RunStep,
} from "../api/runs-api";

export const DELETED_DOCUMENT = "Deleted document";
/** What a pause left alone becomes (the engine's PAUSE_MAX). */
export const UNANSWERED =
  "Left paused for 24 hours, the run ends with what it has read.";

const STEP_WORDS: Record<string, string> = {
  planned: "Waiting for its turn",
  running: "Being read",
  done: "Read",
  reused: "Already read in an earlier run, reused",
  failed: "Not read",
  cancelled: "Left out",
};

/** "3 items", with the figure written as tokens are. */
export const countOf = (value: number, one: string, many: string) =>
  `${tokens(value)} ${value === 1 ? one : many}`;

export function stepStatusWords(step: RunStep): string {
  return STEP_WORDS[step.status] ?? "Not read";
}

/** What a step does, in words. */
export function stepKind(step: RunStep, steps: readonly RunStep[]): string {
  if (step.kind === "transcribe") return "Reading its page images";
  const parts = steps.filter(
    (one) => one.kind === step.kind && one.document_id === step.document_id,
  );
  return parts.length > 1
    ? `Reading its text, part ${parts.indexOf(step) + 1} of ${parts.length}`
    : "Reading its text";
}

/** The first line of the run's state, the one a screen reader hears when it changes. */
export function runHeadline(run: Run): string {
  switch (run.status) {
    case "running":
      if (run.cancel_requested) return "Stopping the run.";
      return run.waiting !== null
        ? "The run is waiting for its turn."
        : "The agent is reading your documents.";
    case "paused":
      return run.paused_reason === PAUSED_ON_CEILING
        ? WORDING.readingPausedAtLimit
        : "The run is paused.";
    case "done":
      return run.partial
        ? "The run is finished, but not everything was read."
        : "The run is finished. Everything you selected was read.";
    case "failed":
      // A run the engine closed keeps what it had read: its reason says so.
      return run.closed_reason
        ? "The run failed."
        : "The run failed. Nothing was read.";
    default:
      return "The run was cancelled.";
  }
}

/** A word for tests and styles, never shown. */
export function stateOf(run: Run): string {
  if (run.status === "paused")
    return run.paused_reason === PAUSED_ON_CEILING ? "ceiling" : "paused";
  if (run.status === "done" && run.partial) return "partial";
  return run.status;
}

/** The selected documents whose every step was reused from an earlier run. */
export function reusedDocuments(run: Run): number {
  return run.document_ids.filter((documentId) => {
    const steps = run.steps.filter((step) => step.document_id === documentId);
    return steps.length > 0 && steps.every((step) => step.status === "reused");
  }).length;
}

/** Why a selected document has nothing read, in a sentence. */
export function whyUnread(run: Run, documentId: string): string {
  const steps = run.steps.filter((step) => step.document_id === documentId);
  if (steps.some((step) => step.document_name === null))
    return "it was deleted before it was read.";
  if (run.secret_document_ids.includes(documentId))
    return "its text may hold a secret, such as a password or a key, so it was not read. Check it under Documents, confirm it, then start a new run.";
  const failed = steps.find((step) => step.error);
  if (failed?.error) return failed.error;
  if (run.status === "cancelled") return "the run was cancelled before it.";
  if (steps.length === 0) return "it did not fit in this run.";
  return "the run ended before it was read.";
}
