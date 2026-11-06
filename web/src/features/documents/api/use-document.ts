import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import {
  documentKey,
  fitDocument,
  getDocument,
  patchDocument,
  versionsKey,
  type DocumentOut,
  type Generation,
  type Language,
} from "./documents-api";
import {
  postingDocumentsKey,
  type GenerationBrief,
} from "./posting-documents-api";
import { templatesKey } from "./templates-api";
import { pollInterval } from "../helpers/labels";

/** One document as the api last answered it. */
export function useDocument(
  documentId: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: documentKey(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
    ...options,
  });
}

/** One document, read again while its page is drawn or while `generation` is open. */
export function useLiveDocument(
  documentId: string,
  generation: Generation | GenerationBrief | null,
) {
  return useQuery({
    queryKey: documentKey(documentId),
    queryFn: ({ signal }) => getDocument(documentId, signal),
    refetchInterval: (current) =>
      pollInterval(current.state.data?.render.state ?? "idle", generation),
  });
}

/** Reads the document's versions and the templates again, once its design capture is done. */
export function useRefreshCaptured(documentId: string) {
  const client = useQueryClient();
  return useCallback(() => {
    void client.invalidateQueries({ queryKey: versionsKey(documentId) });
    void client.invalidateQueries({ queryKey: templatesKey });
  }, [client, documentId]);
}

/** Changes the language of a document's page; the answer goes to the cache. */
export function useDocumentLanguage(
  documentId: string,
  on: { onError: (error: Error) => void },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (value: Language) =>
      patchDocument(documentId, { language: value }),
    onSuccess: (answer) => {
      client.setQueryData(documentKey(documentId), answer);
    },
    onError: on.onError,
  });
}

/** "Make it fit" from the document view: then the document and its posting's list are read again. */
export function useFitDocument(document: DocumentOut) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => fitDocument(document.id),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: documentKey(document.id) });
      if (document.posting_id !== null)
        void client.invalidateQueries({
          queryKey: postingDocumentsKey(document.posting_id),
        });
    },
  });
}
