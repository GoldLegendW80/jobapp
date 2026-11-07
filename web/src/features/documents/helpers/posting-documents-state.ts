import { type Kind, type WaitReason } from "../api/documents-api";
import {
  type PostingDocument,
  type PostingDocuments,
} from "../api/posting-documents-api";
import { isOpen, POLL_MS, pollInterval } from "./labels";

/**
 * How long the section keeps reading the documents after the CV's generation closes, for the
 * letter that follows it: the worker creates the letter's generation just after the CV's closes.
 */
export const LETTER_FOLLOW_MS = 15_000;

/** What a card says first. Open work wins, then a failure, then the state of the latest version. */
export type CardState =
  | { tag: "waiting"; reason: WaitReason | null }
  | { tag: "writing" }
  | { tag: "failed"; error: string | null }
  | { tag: "drawing" }
  | { tag: "sent"; at: string }
  | { tag: "overflow" }
  | { tag: "ready" }
  | { tag: "unrendered"; message: string | null }
  | { tag: "follows" }
  | { tag: "empty" };

/**
 * A document is written only on a click (documents D29): with none, the card says so. A letter
 * not written yet while the CV's hiring panel runs (`afterPanel`) follows the panel's end: the
 * card says so and offers no Write meanwhile.
 */
export function cardState(
  document: PostingDocument | undefined,
  afterPanel = false,
): CardState {
  const generation = document?.generation ?? null;
  if (afterPanel && !isOpen(generation) && (document?.latest ?? null) === null)
    return { tag: "follows" };
  if (isOpen(generation) && generation !== null) {
    return generation.status === "waiting"
      ? { tag: "waiting", reason: generation.wait_reason }
      : { tag: "writing" };
  }
  if (generation?.status === "failed")
    return { tag: "failed", error: generation.error ?? null };
  const latest = document?.latest ?? null;
  if (document !== undefined && latest !== null) {
    if (latest.frozen_at !== null && document.sent.includes(latest.number))
      return { tag: "sent", at: latest.frozen_at };
    if (document.render.state !== "idle") return { tag: "drawing" };
    if (latest.fit === "overflow") return { tag: "overflow" };
    if (latest.fit === "one_page") return { tag: "ready" };
    return { tag: "unrendered", message: document.render.message };
  }
  if (document !== undefined && document.render.state !== "idle")
    return { tag: "drawing" };
  return { tag: "empty" };
}

/**
 * True while a document still changes by itself: a generation open, a page being drawn, or the
 * hiring panel reviewing and rebuilding it.
 */
export function isBusy(document: PostingDocument): boolean {
  return (
    isOpen(document.generation) ||
    document.render.state !== "idle" ||
    document.panel_running === true
  );
}

/**
 * How soon to read the posting's documents again: the soonest of what each one is waiting for,
 * every POLL_MS while a hiring panel runs, and while a letter is expected and its generation is
 * not there yet.
 */
export function postingPollInterval(
  documents: PostingDocuments | undefined,
  expectLetter = false,
): number | false {
  let next: number | false =
    expectLetter && !isOpen(documentOf(documents, "letter")?.generation)
      ? POLL_MS
      : false;
  for (const one of documents?.documents ?? []) {
    // A running panel ends with a new version and, after a CV, its letter: read until it ends.
    const every =
      one.panel_running === true
        ? POLL_MS
        : pollInterval(one.render.state, one.generation);
    if (every !== false && (next === false || every < next)) next = every;
  }
  return next;
}

export function documentOf(
  documents: PostingDocuments | undefined,
  kind: Kind,
): PostingDocument | undefined {
  return documents?.documents.find((one) => one.kind === kind);
}
