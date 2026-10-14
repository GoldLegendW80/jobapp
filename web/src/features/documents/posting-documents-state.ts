import {
  type Kind,
  type PostingDocument,
  type PostingDocuments,
  type WaitReason,
} from "./documents-api";
import { isOpen, pollInterval } from "./labels";

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
  | { tag: "under"; fit: number; min: number }
  | { tag: "manual" }
  | { tag: "empty" };

export function cardState(
  document: PostingDocument | undefined,
  fit: number | null,
  threshold: { auto: boolean; min: number } | undefined,
): CardState {
  const generation = document?.generation ?? null;
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
  if (threshold === undefined) return { tag: "empty" };
  if (!threshold.auto) return { tag: "manual" };
  if (fit !== null && fit < threshold.min)
    return { tag: "under", fit, min: threshold.min };
  return { tag: "empty" };
}

/** How soon to read the posting's documents again: the soonest of what each one is waiting for. */
export function postingPollInterval(
  documents: PostingDocuments | undefined,
): number | false {
  let next: number | false = false;
  for (const one of documents?.documents ?? []) {
    const every = pollInterval(one.render.state, one.generation);
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
