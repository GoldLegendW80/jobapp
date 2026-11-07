import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { jobKey } from "@/features/jobs/api/jobs-api";

import { fitDocument, type Kind } from "./documents-api";
import {
  getPostingDocuments,
  postingDocumentsKey,
  writeNow,
} from "./posting-documents-api";
import { postingPollInterval } from "../helpers/posting-documents-state";

/**
 * A posting's CV and letter, read again while one of them is written or drawn, and while
 * `expectLetter` until the letter's generation shows.
 */
export function usePostingDocuments(jobId: string, expectLetter?: boolean) {
  return useQuery({
    queryKey: postingDocumentsKey(jobId),
    queryFn: ({ signal }) => getPostingDocuments(jobId, signal),
    refetchInterval: (current) =>
      postingPollInterval(current.state.data, expectLetter),
  });
}

/**
 * Once the work on a posting's documents ends (`busy` turns false), the posting is read again:
 * the render that ends it may have moved the posting to `cv_ready`.
 */
export function useRefreshWhenWorkEnds(jobId: string, busy: boolean) {
  const client = useQueryClient();
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      void client.invalidateQueries({ queryKey: jobKey(jobId) });
    }
    wasBusy.current = busy;
  }, [busy, client, jobId]);
}

/** "Write now" on a posting; its documents are read again after either outcome. */
export function useWriteNow(jobId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { kinds: Kind[]; base_document_id?: string }) =>
      writeNow(jobId, body),
    onSettled: () =>
      client.invalidateQueries({ queryKey: postingDocumentsKey(jobId) }),
  });
}

/** "Make it fit" on a posting's card; its documents are read again after either outcome. */
export function useFitPostingDocument(jobId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => fitDocument(id),
    onSettled: () =>
      client.invalidateQueries({ queryKey: postingDocumentsKey(jobId) }),
  });
}
