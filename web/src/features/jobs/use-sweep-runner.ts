/**
 * State and actions of the Find jobs flow (find-jobs.tsx): start a sweep, follow the one
 * running (started here, elsewhere, or an automatic one found when the page opens), keep its
 * latest progress, and the summary to show once it ends.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { describeError, errorDetail, isApiError } from "@/lib/api";

import { jobsKey } from "./api";
import {
  listSweeps,
  runningSweepOf,
  startSweep,
  sweepsKey,
  watchSweep,
  type Sweep,
  type SweepDetail,
} from "./sweeps-api";

export interface SweepRunner {
  /** The sweep being followed, with its latest progress. */
  running: Sweep | null;
  starting: boolean;
  start: () => void;
  summary: SweepDetail | null;
  showSummary: (sweep: SweepDetail | null) => void;
}

export function useSweepRunner(): SweepRunner {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [started, setStarted] = useState<string | null>(null);
  const [ended, setEnded] = useState<ReadonlySet<string>>(new Set());
  const [progress, setProgress] = useState<Sweep | null>(null);
  const [starting, setStarting] = useState(false);
  const [summary, setSummary] = useState<SweepDetail | null>(null);

  const history = useQuery({
    queryKey: sweepsKey,
    queryFn: ({ signal }) => listSweeps(signal),
  });
  const latest = history.data?.items[0];
  const listed =
    latest?.status === "running" && !ended.has(latest.id) ? latest : null;
  // The sweep to follow: the one started here, else one the list says is running.
  const target =
    started && !ended.has(started) ? started : (listed?.id ?? null);

  useEffect(() => {
    if (!target) return undefined;
    const finish = () => {
      setEnded((old) => new Set(old).add(target));
      setProgress(null);
      void client.invalidateQueries({ queryKey: sweepsKey });
    };
    return watchSweep(target, {
      onProgress: setProgress,
      onDone: (sweep) => {
        finish();
        setSummary(sweep);
        void client.invalidateQueries({ queryKey: jobsKey });
      },
      onError: finish,
    });
  }, [target, client]);

  const start = useCallback(() => {
    setStarting(true);
    startSweep().then(
      (sweep) => {
        setStarting(false);
        setProgress(sweep);
        setStarted(sweep.id);
        void client.invalidateQueries({ queryKey: sweepsKey });
      },
      (error: unknown) => {
        setStarting(false);
        const already = runningSweepOf(error);
        if (already) {
          toast.info("A sweep is already running: following it.");
          setEnded((old) => {
            const next = new Set(old);
            next.delete(already);
            return next;
          });
          setStarted(already);
          return;
        }
        if (isApiError(error) && error.status === 422) {
          // No enabled board, or a stored search section to save again: both are fixed in
          // the Search tab of the profile.
          toast.error(errorDetail(error) ?? "No board to sweep.", {
            action: {
              label: "Open the profile",
              onClick: () => {
                void navigate("/profile?tab=search");
              },
            },
          });
          return;
        }
        toast.error(`The sweep did not start. ${describeError(error)}`);
      },
    );
  }, [client, navigate]);

  let running: Sweep | null = null;
  if (target) {
    if (progress?.id === target) running = progress;
    else if (listed?.id === target) running = listed;
    else running = { ...EMPTY_PROGRESS, id: target };
  }
  return { running, starting, start, summary, showSummary: setSummary };
}

/** What the progress line shows for a sweep whose first update has not arrived yet. */
const EMPTY_PROGRESS: Sweep = {
  id: "",
  trigger: "manual",
  status: "running",
  started_at: new Date(0).toISOString(),
  finished_at: null,
  error: null,
  boards_total: 0,
  boards_done: 0,
  boards_ok: 0,
  boards_failed: [],
  current_board: null,
  seen: 0,
  matched: 0,
  new: 0,
  duplicates: 0,
  skipped_by_rule: 0,
  duration: null,
};
