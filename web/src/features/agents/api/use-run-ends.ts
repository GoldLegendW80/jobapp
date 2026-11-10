import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  claimRun,
  firstSight,
  newlyEnded,
  releaseRun,
  type Baseline,
} from "../helpers/run-ends";
import {
  ACTIVE_FLOW_STATUSES,
  getWorkflowRun,
  workflowRunKey,
  type WorkflowRunDetail,
} from "./activity-api";
import { useActivityCount } from "./use-activity";

/**
 * Calls `onEnd` once with the summary of each of the person's workflow runs that ends while the
 * page is open, read from the Activity tab's count (no read of its own on an interval): a run
 * that ended before the first count, or that another tab of this browser announced, is left out.
 */
export function useRunEnds(onEnd: (run: WorkflowRunDetail) => void) {
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
            else announce.current(summary);
          },
          () => {
            giveBack(run.id);
          },
        );
    }
  }, [client, data, dataUpdatedAt]);
}
