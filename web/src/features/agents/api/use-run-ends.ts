import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { getSweep } from "@/features/jobs/api/sweeps-api";

import {
  claimRun,
  firstSight,
  newlyEnded,
  releaseRun,
  type Baseline,
  type RunEnd,
} from "../helpers/run-ends";
import {
  ACTIVE_FLOW_STATUSES,
  getWorkflowRun,
  workflowRunKey,
  type WorkflowRunDetail,
} from "./activity-api";
import { useActivityCount } from "./use-activity";

/** A job search's run is named by its sweep: the saved search that sweep ran, or nothing for the
 * main search or when the sweep cannot be read (the toast then keeps its words). */
async function withSearch(run: WorkflowRunDetail): Promise<RunEnd> {
  if (run.workflow !== "job_search") return run;
  try {
    const search = (await getSweep(run.id)).search;
    return search && search.id !== "main"
      ? { ...run, search: search.name }
      : run;
  } catch {
    return run;
  }
}

/**
 * Calls `onEnd` once with the summary of each of the person's workflow runs that ends while the
 * page is open, read from the Activity tab's count (no read of its own on an interval): a run
 * that ended before the first count, or that another tab of this browser announced, is left out.
 */
export function useRunEnds(onEnd: (run: RunEnd) => void) {
  const client = useQueryClient();
  const count = useActivityCount();
  const baseline = useRef<Baseline | null>(null);
  const handled = useRef(new Set<string>());
  const announce = useRef(onEnd);
  useEffect(() => {
    announce.current = onEnd;
  }, [onEnd]);

  const { data, dataUpdatedAt } = count;
  useEffect(() => {
    const giveBack = (id: string) => {
      handled.current.delete(id);
      releaseRun(id);
    };
    if (!data) return;
    if (!baseline.current) {
      baseline.current = firstSight(data.ended);
      return;
    }
    for (const run of newlyEnded(baseline.current, data.ended)) {
      if (handled.current.has(run.id)) continue;
      handled.current.add(run.id);
      if (!claimRun(run.id)) continue;
      client
        .query({
          queryKey: workflowRunKey(run.id),
          queryFn: ({ signal }) => getWorkflowRun(run.id, signal),
          staleTime: 0,
        })
        .then(
          (summary) => {
            // Started again since (a rebuild joins its run): its next end is announced.
            if (ACTIVE_FLOW_STATUSES.has(summary.status)) giveBack(run.id);
            else
              void withSearch(summary).then((ended) => {
                announce.current(ended);
              });
          },
          () => {
            giveBack(run.id);
          },
        );
    }
  }, [client, data, dataUpdatedAt]);
}
