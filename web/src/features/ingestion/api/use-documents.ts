import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  confirmDocument,
  deleteDocument,
  documentsKey,
  documentTextKey,
  getDocumentText,
  listDocuments,
  pasteDocument,
  type DocumentList,
  type StoredDocument,
} from "./documents-api";
import { watchInterval } from "../helpers/labels";

/** The person's documents, their limits and what they use. */
export function useDocuments(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    ...options,
  });
}

/** The person's documents, read again by themselves only while a parser is at work on one. */
export function useWatchedDocuments() {
  return useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    refetchInterval: (current) => watchInterval(current.state.data?.items),
  });
}

/** The text kept from a document, a page at a time; dropped as soon as nothing shows it. */
export function useDocumentText(documentId: string) {
  return useInfiniteQuery({
    queryKey: documentTextKey(documentId),
    queryFn: ({ pageParam, signal }) =>
      getDocumentText(documentId, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    // Dropped as soon as the dialog closes: a document's text is not kept around.
    gcTime: 0,
  });
}

/** One page of a document's text from `from`, around a quote; dropped as soon as nothing shows it. */
export function useQuoteText(documentId: string, from: number) {
  return useQuery({
    queryKey: ["ingestion", "documents", documentId, "quote", from],
    queryFn: ({ signal }) => getDocumentText(documentId, from, signal),
    // Dropped as soon as the dialog closes: a document's text is not kept around.
    gcTime: 0,
  });
}

/** Keeps a document flagged as holding a secret; the answer replaces it in the cached list. */
export function useConfirmDocument(documentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => confirmDocument(documentId),
    onSuccess: (confirmed) => {
      client.setQueryData<DocumentList>(documentsKey, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === confirmed.id ? confirmed : item,
              ),
            }
          : old,
      );
    },
  });
}

/** Deletes a document for good. */
export function useDeleteDocument(
  documentId: string,
  on: { onDeleted: () => void },
) {
  return useMutation({
    mutationFn: () => deleteDocument(documentId),
    onSuccess: () => {
      on.onDeleted();
    },
  });
}

/** Stores a pasted text as a document. */
export function usePasteDocument(on: {
  onPasted: (stored: StoredDocument) => void;
  onError: (error: Error) => void;
}) {
  return useMutation({
    mutationFn: pasteDocument,
    onSuccess: on.onPasted,
    onError: on.onError,
  });
}
