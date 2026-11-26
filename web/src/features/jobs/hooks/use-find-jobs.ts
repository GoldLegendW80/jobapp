import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import type { SweepRunner } from "../api/use-sweep-runner";

/** The screen of Find jobs: Your searches, or the form of one search (id "" for a new one). */
export type FindJobsScreen = { kind: "list" } | { kind: "edit"; id: string };

/** The Find jobs setup of /jobs, which its two buttons open: the header's and, while the list
 * is empty, the empty list's (one setup, so one search runner and one `?find=1`). */
export interface FindJobs {
  runner: SweepRunner;
  open: boolean;
  /** The screen it opens on. */
  screen: FindJobsScreen;
  /** Open the setup; focus goes back to `from` when it closes. */
  show: (from: HTMLElement) => void;
  close: () => void;
  returnFocus: () => void;
}

export function useFindJobs(runner: SweepRunner): FindJobs {
  // `/jobs?find=1` (the wizard's Start with an empty list, Discover's empty deck) opens the
  // main search's form at once, to add websites; the word then leaves the address, so a reload
  // does not open it again.
  const [params, setParams] = useSearchParams();
  const asked = params.get("find") === "1";
  const [open, setOpen] = useState(asked);
  const [screen, setScreen] = useState<FindJobsScreen>(
    asked ? { kind: "edit", id: "main" } : { kind: "list" },
  );
  const from = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!asked) return;
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        next.delete("find");
        return next;
      },
      { replace: true },
    );
  }, [asked, setParams]);
  return {
    runner,
    open,
    screen,
    show: (element) => {
      from.current = element;
      setScreen({ kind: "list" });
      setOpen(true);
    },
    close: () => {
      setOpen(false);
    },
    returnFocus: () => {
      // Opened from the address, or its button left the page meanwhile: the Find jobs shown.
      const target = from.current?.isConnected
        ? from.current
        : document.querySelector<HTMLElement>("[data-find-jobs]");
      target?.focus();
    },
  };
}
