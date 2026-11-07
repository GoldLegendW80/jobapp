/**
 * A posting's notes as the person types them, saved after a pause in typing or when the box
 * loses focus, never twice for the same text, and kept with a Retry when saving fails.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { JobDetail } from "../api/jobs-api";
import {
  failure,
  NOTES_DEBOUNCE_MS,
  NOTES_MAX,
  type Save,
} from "../api/use-job-editor";

export type NotesState = "idle" | "dirty" | "saving" | "saved" | "error";

export function useNotesAutosave(job: JobDetail, save: Save) {
  const [draft, setDraft] = useState(job.notes);
  const [state, setState] = useState<NotesState>("idle");
  const [error, setError] = useState("");
  // The text the server holds as far as this editor knows.
  const saved = useRef(job.notes);
  const inFlight = useRef<string | null>(null);
  const latest = useRef(draft);
  useEffect(() => {
    latest.current = draft;
  });

  // A change from elsewhere (a bulk note, a reload) replaces a draft the user has not touched.
  const [seen, setSeen] = useState(job.notes);
  if (job.notes !== seen) {
    setSeen(job.notes);
    if (draft === seen) setDraft(job.notes);
  }

  const commit = useCallback(
    (text: string) => {
      if (text === saved.current || text === inFlight.current) return;
      if (text.length > NOTES_MAX) return;
      inFlight.current = text;
      setState("saving");
      save({ notes: text }).then(
        (result) => {
          saved.current = result.notes;
          latest.current =
            text === latest.current ? result.notes : latest.current;
          inFlight.current = null;
          setState(latest.current === result.notes ? "saved" : "dirty");
        },
        (failed: unknown) => {
          inFlight.current = null;
          setError(failure(failed));
          setState("error");
        },
      );
    },
    [save],
  );

  useEffect(() => {
    if (draft === saved.current || state === "error") return;
    const timer = setTimeout(() => {
      commit(draft);
    }, NOTES_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [draft, commit, state]);

  const tooLong = draft.length > NOTES_MAX;

  return {
    draft,
    state,
    error,
    tooLong,
    edit: (text: string) => {
      setDraft(text);
      setState("dirty");
      setError("");
    },
    /** Saves now (the box lost focus), unless the text is too long. */
    flush: () => {
      if (!tooLong) commit(draft);
    },
    retry: () => {
      setState("dirty");
      commit(draft);
    },
  };
}
