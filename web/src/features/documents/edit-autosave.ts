import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { isApiError } from "@/lib/api";

import {
  documentKey,
  failureSentence,
  saveDraft,
  staleDraft,
  type DocumentOut,
} from "./documents-api";
import {
  fieldKey,
  localPath,
  setAt,
  toPayload,
  withDefaults,
  type Draft,
  type Path,
} from "./edit-content";

/** A save starts this long after the last keystroke (SPEC-documents.md, "Pages", Edit). */
export const AUTOSAVE_MS = 1000;

export type SaveStatus =
  | { state: "saved" }
  | { state: "waiting" }
  | { state: "saving" }
  /** `fields`: what the api refused, by the form's path. */
  | { state: "failed"; message: string; fields: Map<string, string> }
  /** The draft changed elsewhere since this form read it: `theirs` is the current one. */
  | { state: "conflict"; theirs: DocumentOut };

interface Unsaved {
  draft: Draft;
  revision: number;
}

/**
 * Edits not saved yet when the view closed, by document: reopening it sends them again. Kept in
 * the query cache, so a sign out or a sign in forgets them with every other answer.
 */
function unsavedOf(queryClient: QueryClient, id: string) {
  const key = ["documents-unsaved", id];
  return {
    get: () => queryClient.getQueryData<Unsaved>(key),
    set: (value: Unsaved) => {
      // Nothing observes it: kept until sent or forgotten, never collected.
      queryClient.setQueryDefaults(key, { gcTime: Infinity });
      queryClient.setQueryData<Unsaved>(key, value);
    },
    drop: () => {
      queryClient.removeQueries({ queryKey: key, exact: true });
    },
  };
}

const REFUSED: Record<string, string> = {
  empty: "This cannot be empty.",
  too_long: "This is longer than allowed.",
  too_many: "There are more entries than allowed.",
  bad_format: "Write a year or a month: 2021 or 2021-03.",
  duplicate_employer: "This employer is named twice.",
  content_too_large: "The whole document is too large to save.",
};

function refusal(error: unknown, draft: Draft): Map<string, string> {
  const fields = new Map<string, string>();
  if (!isApiError(error) || error.status !== 422) return fields;
  const detail = (error.body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return fields;
  for (const one of detail as { path?: unknown; code?: unknown }[]) {
    if (typeof one.path !== "string") continue;
    const code = typeof one.code === "string" ? one.code : "";
    fields.set(
      fieldKey(localPath(draft, one.path)),
      REFUSED[code] ?? "This cannot be saved as it is.",
    );
  }
  return fields;
}

/**
 * One form's saves, in order: never two at once, each with `If-Match` of the revision the one
 * before answered. Typing during a save sends the newer text after it. A 409 stops the saves
 * until the person picks a side; a draft changed elsewhere is taken only while nothing typed
 * here waits to be saved.
 */
function createSaver(
  document: DocumentOut,
  queryClient: QueryClient,
  set: {
    draft: (draft: Draft) => void;
    status: (status: SaveStatus) => void;
    saved: (document: DocumentOut) => void;
    replaced: () => void;
  },
) {
  const id = document.id;
  const unsaved = unsavedOf(queryClient, id);
  const stashed = unsaved.get();
  let latest = stashed?.draft ?? withDefaults(document);
  let revision = stashed?.revision ?? document.draft_revision;
  let sent = JSON.stringify(toPayload(withDefaults(document)));
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight = false;
  let again = false;
  let conflict = false;

  const dirty = () => JSON.stringify(toPayload(latest)) !== sent;
  const stop = () => {
    clearTimeout(timer);
    timer = undefined;
  };

  async function save(): Promise<void> {
    stop();
    if (conflict) return;
    if (inFlight) {
      again = true;
      return;
    }
    const payload = toPayload(latest);
    const sending = JSON.stringify(payload);
    if (sending === sent) {
      unsaved.drop();
      set.status({ state: "saved" });
      return;
    }
    inFlight = true;
    set.status({ state: "saving" });
    try {
      const answer = await saveDraft(id, payload, revision);
      revision = answer.draft_revision;
      sent = sending;
      queryClient.setQueryData(documentKey(id), answer);
      set.saved(answer);
      if (dirty()) {
        if (unsaved.get() !== undefined)
          unsaved.set({ draft: latest, revision });
        set.status({ state: "waiting" });
      } else {
        unsaved.drop();
        set.status({ state: "saved" });
      }
    } catch (error) {
      const stale = staleDraft(error);
      if (stale !== undefined) {
        conflict = true;
        again = false;
        set.status({ state: "conflict", theirs: stale.document });
      } else {
        set.status({
          state: "failed",
          message: failureSentence(error),
          fields: refusal(error, latest),
        });
      }
    } finally {
      inFlight = false;
      if (again && !conflict) {
        again = false;
        void save();
      }
    }
  }

  function adopt(next: DocumentOut) {
    stop();
    conflict = false;
    again = false;
    latest = withDefaults(next);
    revision = next.draft_revision;
    sent = JSON.stringify(toPayload(latest));
    unsaved.drop();
    set.draft(latest);
    set.saved(next);
    set.status({ state: "saved" });
    set.replaced();
  }

  return {
    initial: { draft: latest, pending: stashed !== undefined },
    /** One field typed: the save waits AUTOSAVE_MS for the next keystroke. */
    edit(path: Path, value: unknown) {
      latest = setAt(latest, path, value) as Draft;
      set.draft(latest);
      if (conflict) return;
      stop();
      set.status({ state: "waiting" });
      timer = setTimeout(() => void save(), AUTOSAVE_MS);
    },
    save,
    /** Leaving a field saves what waits at once. */
    flush() {
      if (timer !== undefined) void save();
    },
    /** A draft read again (the poll, a restore, a writer): taken when nothing waits here. */
    seen(next: DocumentOut) {
      if (next.draft_revision <= revision) return;
      if (inFlight || conflict || timer !== undefined || dirty()) return;
      adopt(next);
    },
    keepMine(theirs: DocumentOut) {
      conflict = false;
      revision = theirs.draft_revision;
      sent = "";
      void save();
    },
    takeTheirs(theirs: DocumentOut) {
      queryClient.setQueryData(documentKey(id), theirs);
      adopt(theirs);
    },
    /** The view closes: what is not saved is kept for the next time and sent now. */
    close() {
      if (timer === undefined && !inFlight && !conflict && !dirty()) return;
      unsaved.set({ draft: latest, revision });
      void save();
    },
  };
}

export type Saver = ReturnType<typeof createSaver>;

/** The draft of `document` as the form edits it, saved by itself (see `createSaver`). */
export function useAutosave(document: DocumentOut) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SaveStatus>({ state: "saved" });
  const [saved, setSaved] = useState(document);
  // Bumped when the draft is replaced from outside, so inputs with their own text start again.
  const [round, setRound] = useState(0);
  // Null until the first change: the saver's own first draft (a stashed one, else the document's).
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saver] = useState(() =>
    createSaver(document, queryClient, {
      draft: setDraft,
      status: setStatus,
      saved: setSaved,
      replaced: () => {
        setRound((value) => value + 1);
      },
    }),
  );

  useEffect(() => {
    if (saver.initial.pending) void saver.save();
    return () => {
      saver.close();
    };
  }, [saver]);

  useEffect(() => {
    saver.seen(document);
  }, [saver, document]);

  const busy = status.state !== "saved";
  useEffect(() => {
    if (!busy) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [busy]);

  return { draft: draft ?? saver.initial.draft, status, saved, round, saver };
}
