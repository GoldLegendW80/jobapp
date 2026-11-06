import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { jobKey, jobsKey, type JobDetail } from "@/features/jobs/api/jobs-api";

import {
  acceptProposal,
  jobProposalsKey,
  listJobProposals,
  rejectProposal,
  type Proposal,
} from "./proposals-api";

/**
 * What the agents proposed for one job: read every `pollMs` while its run is `running`, and once
 * more when the run ends.
 */
export function useJobProposals(
  jobId: string,
  running: boolean,
  pollMs: number,
) {
  const client = useQueryClient();
  const proposals = useQuery({
    queryKey: jobProposalsKey(jobId),
    queryFn: ({ signal }) => listJobProposals(jobId, signal),
    refetchInterval: running ? pollMs : false,
  });

  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void client.invalidateQueries({ queryKey: jobProposalsKey(jobId) });
    }
    wasRunning.current = running;
  }, [running, client, jobId]);

  return proposals;
}

/** A decision changes the job: its proposals, the job and the list are read again, also on a refusal. */
function useRefreshJob(jobId: string) {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: jobProposalsKey(jobId) });
    void client.invalidateQueries({ queryKey: jobKey(jobId) });
    void client.invalidateQueries({ queryKey: [...jobsKey, "list"] });
  };
}

type DecisionHandlers<Variables> = Pick<
  UseMutationOptions<Proposal, Error, Variables>,
  "onSuccess" | "onError"
>;

/** Accepts a proposal on the job as last read, with the note as the person `edited` it. */
export function useAcceptProposal(
  job: Pick<JobDetail, "id" | "updated_at">,
  proposalId: string,
  handlers: DecisionHandlers<string | undefined>,
) {
  const refresh = useRefreshJob(job.id);
  return useMutation({
    mutationFn: (edited?: string) =>
      acceptProposal(proposalId, {
        updated_at: job.updated_at,
        ...(edited === undefined ? {} : { edited_value: edited }),
      }),
    onSuccess: (...args) => {
      handlers.onSuccess?.(...args);
      refresh();
    },
    onError: (...args) => {
      handlers.onError?.(...args);
      refresh();
    },
  });
}

/** Rejects a proposal. */
export function useRejectProposal(
  jobId: string,
  proposalId: string,
  handlers: DecisionHandlers<void>,
) {
  const refresh = useRefreshJob(jobId);
  return useMutation({
    mutationFn: () => rejectProposal(proposalId),
    onSuccess: (...args) => {
      handlers.onSuccess?.(...args);
      refresh();
    },
    onError: (...args) => {
      handlers.onError?.(...args);
      refresh();
    },
  });
}
