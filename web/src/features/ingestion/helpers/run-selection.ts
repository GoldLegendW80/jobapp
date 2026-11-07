/** Which documents a run may be asked to read, and the server's word on a selection it refuses. */
import { errorDetail, isApiError } from "@/lib/api";

import type { IngestionDocument } from "../api/documents-api";

/**
 * A document the run can read now: its text is there, or its page images wait for the agent,
 * which transcribes them in the first steps of the run. The estimate has the last word on it.
 */
export function canBeChosen(document: IngestionDocument): boolean {
  return (
    document.readable &&
    (document.status === "done" || document.status === "awaiting_transcription")
  );
}

/** The server's sentence for a selection it refuses (over a limit of a run): what to change. */
export function selectionRefusal(error: unknown): string | undefined {
  return isApiError(error) && error.kind === "http" && error.status === 422
    ? errorDetail(error)
    : undefined;
}
