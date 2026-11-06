import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listMessages,
  messagesKey,
  sendMessage,
  type Message,
} from "./chat-api";
import {
  cancelGeneration,
  generationKey,
  getGeneration,
} from "./documents-api";
import { consentKey } from "./spend-api";
import { isOpen, POLL_MS } from "../helpers/labels";

/** The conversation of a document's Chat view. */
export function useMessages(documentId: string) {
  return useQuery({
    queryKey: messagesKey(documentId),
    queryFn: ({ signal }) => listMessages(documentId, "content", signal),
  });
}

/** A generation, read again while it is open; nothing is asked while `generationId` is null. */
export function useGeneration(generationId: string | null) {
  return useQuery({
    queryKey: generationKey(generationId ?? "none"),
    queryFn: ({ signal }) => getGeneration(generationId ?? "", signal),
    enabled: generationId !== null,
    refetchInterval: (current) =>
      current.state.data === undefined || isOpen(current.state.data)
        ? POLL_MS
        : false,
  });
}

/**
 * Sends a message of the Chat view. The message sent joins the cached conversation; on a failure
 * the conversation and the consent are read again.
 */
export function useSendMessage(
  documentId: string,
  on: {
    onStart: () => void;
    onSent: () => void;
    onError: (error: Error) => void;
  },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => sendMessage(documentId, text),
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (sent) => {
      client.setQueryData<Message[]>(messagesKey(documentId), (old) => [
        ...(old ?? []).filter((one) => one.id !== sent.message.id),
        sent.message,
      ]);
      on.onSent();
    },
    onError: (failure) => {
      on.onError(failure);
      void client.invalidateQueries({ queryKey: messagesKey(documentId) });
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });
}

/** Stop: ends the answer being written; the generation and the conversation are read again. */
export function useStopGeneration(
  documentId: string,
  on: { onStart: () => void; onError: (error: Error) => void },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (generationId: string) => cancelGeneration(generationId),
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (_, generationId) => {
      void client.invalidateQueries({ queryKey: generationKey(generationId) });
      void client.invalidateQueries({ queryKey: messagesKey(documentId) });
    },
    onError: on.onError,
  });
}
