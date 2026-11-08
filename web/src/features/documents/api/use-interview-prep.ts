import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptProposal,
  rejectProposal,
} from "@/features/agents/api/proposals-api";
import { profileKey } from "@/features/profile/api/profile-api";

import {
  getInterviewPrep,
  interviewPrepKey,
  keepStories,
  prepareInterview,
  savePrep,
  sendPractice,
  type Debrief,
  type InterviewPrepRead,
} from "./interview-prep-api";

const POLL_MS = 2_000;

/** Whether a writing or a practice answer is on its way: the read is polled until it lands. */
export function isPreparing(prep: InterviewPrepRead | undefined): boolean {
  return (
    prep !== undefined &&
    (prep.practice_open ||
      prep.writing?.status === "running" ||
      prep.writing?.status === "queued")
  );
}

/** The posting's interview preparation, read again every 2 seconds while work runs. */
export function useInterviewPrep(jobId: string) {
  return useQuery({
    queryKey: interviewPrepKey(jobId),
    queryFn: ({ signal }) => getInterviewPrep(jobId, signal),
    refetchInterval: (query) =>
      isPreparing(query.state.data) ? POLL_MS : false,
  });
}

function useRefresh(jobId: string) {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: interviewPrepKey(jobId) });
}

/** Prepare, on the person's click. */
export function usePrepare(jobId: string) {
  const refresh = useRefresh(jobId);
  return useMutation({
    mutationFn: () => prepareInterview(jobId),
    onSettled: refresh,
  });
}

/** One practice message; the answer arrives with the polled read. */
export function usePractice(jobId: string, documentId: string) {
  const refresh = useRefresh(jobId);
  return useMutation({
    mutationFn: (text: string) => sendPractice(documentId, text),
    onSettled: refresh,
  });
}

/** The chosen stories as a proposal for the profile's facts. */
export function useKeepStories(jobId: string, documentId: string) {
  const refresh = useRefresh(jobId);
  return useMutation({
    mutationFn: (indexes: number[]) => keepStories(documentId, indexes),
    onSettled: refresh,
  });
}

/** Accept or reject the pending stories proposal; the profile is read again after an accept. */
export function useDecideStories(jobId: string) {
  const refresh = useRefresh(jobId);
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, accept }: { id: string; accept: boolean }) =>
      accept ? acceptProposal(id, {}) : rejectProposal(id),
    onSettled: async () => {
      await refresh();
      await client.invalidateQueries({ queryKey: profileKey });
    },
  });
}

/** Appends what was asked to the preparation, saved by hand as a new version. */
export function useAddDebrief(jobId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (entry: Debrief) => {
      const current = client.getQueryData<InterviewPrepRead>(
        interviewPrepKey(jobId),
      )?.document;
      if (!current) throw new Error("The preparation is not loaded.");
      const content = {
        ...current.draft,
        debrief: [...current.draft.debrief, entry],
      };
      return savePrep(current.id, content, current.draft_revision);
    },
    onSettled: () =>
      client.invalidateQueries({ queryKey: interviewPrepKey(jobId) }),
  });
}
