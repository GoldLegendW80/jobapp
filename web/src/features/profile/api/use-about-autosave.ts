/**
 * The "About you" text as the person types it, saved in order: never two saves at once, each on
 * the version the one before answered; typing during a save sends the newer text after it. A 409
 * stops the saves until the person loads the latest or keeps theirs.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";

import { AUTOSAVE_MS } from "@/features/documents/api/use-edit-autosave";
import { describeError } from "@/lib/api";

import { aboutKey, saveAbout, staleAbout, type About } from "./about-api";

export type SaveState =
  | { state: "saved" }
  | { state: "waiting" }
  | { state: "saving" }
  | { state: "failed"; message: string }
  | { state: "conflict"; theirs: About };

interface Snapshot {
  text: string;
  status: SaveState;
}

/** The saves of one document, as a small store the page reads with useSyncExternalStore. */
function createSaver(initial: About, queryClient: QueryClient) {
  let version = initial.version;
  let sent = initial.text;
  let latest = initial.text;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<About | undefined> | undefined;
  let theirs: About | undefined;
  let snapshot: Snapshot = { text: initial.text, status: { state: "saved" } };
  const listeners = new Set<() => void>();

  const publish = (next: Partial<Snapshot>) => {
    snapshot = { ...snapshot, ...next };
    for (const listener of listeners) listener();
  };

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      void save();
    }, AUTOSAVE_MS);
  };

  async function save(): Promise<About | undefined> {
    clearTimeout(timer);
    if (theirs) return undefined;
    if (inFlight) {
      await inFlight;
      return save();
    }
    if (latest === sent) {
      publish({ status: { state: "saved" } });
      return queryClient.getQueryData<About>(aboutKey);
    }
    const sending = latest;
    publish({ status: { state: "saving" } });
    inFlight = saveAbout(version, sending).then(
      (answer) => {
        version = answer.version;
        sent = sending;
        inFlight = undefined;
        queryClient.setQueryData(aboutKey, answer);
        if (latest === sent) publish({ status: { state: "saved" } });
        else {
          publish({ status: { state: "waiting" } });
          schedule();
        }
        return answer;
      },
      (error: unknown) => {
        inFlight = undefined;
        theirs = staleAbout(error);
        publish({
          status: theirs
            ? { state: "conflict", theirs }
            : { state: "failed", message: describeError(error) },
        });
        return undefined;
      },
    );
    return inFlight;
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    snapshot: () => snapshot,
    flush: save,
    dirty: () => latest !== sent,
    change: (next: string) => {
      latest = next;
      if (theirs) {
        publish({ text: next });
        return;
      }
      publish({ text: next, status: { state: "waiting" } });
      schedule();
    },
    /** A text saved elsewhere (a rewrite, Done), taken while nothing typed here waits. */
    adopt: (about: About) => {
      if (latest !== sent || inFlight || theirs) return;
      latest = sent = about.text;
      version = about.version;
      publish({ text: about.text });
    },
    settle: (keep: "theirs" | "mine") => {
      if (!theirs) return;
      const current = theirs;
      theirs = undefined;
      version = current.version;
      queryClient.setQueryData(aboutKey, current);
      if (keep === "theirs") {
        latest = sent = current.text;
        publish({ text: current.text, status: { state: "saved" } });
      } else {
        sent = current.text;
        void save();
      }
    },
  };
}

export function useAboutAutosave(about: About) {
  const queryClient = useQueryClient();
  const [saver] = useState(() => createSaver(about, queryClient));
  const { text, status } = useSyncExternalStore(
    saver.subscribe,
    saver.snapshot,
  );
  useEffect(() => {
    saver.adopt(about);
  }, [saver, about]);
  // Leaving the page sends what waits; closing the tab with it asks first.
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (saver.dirty()) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      if (saver.dirty()) void saver.flush();
    };
  }, [saver]);
  return {
    text,
    status,
    change: saver.change,
    flush: saver.flush,
    settle: saver.settle,
  };
}
