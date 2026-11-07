import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { baseCvsKey } from "./base-cvs-api";
import { documentKey } from "./documents-api";
import {
  acceptConsent,
  consentKey,
  getConsent,
  type Consent,
} from "./consent-api";

/** The consent to what the writer sends to a model, and the model it would use. */
export function useConsent() {
  return useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });
}

/**
 * Records the consent to the text of `version`; the answer goes to the cache. A 422 means the
 * text changed under the open tab: the current one is read again to agree to.
 */
export function useAcceptConsent(on: { onAccepted: () => void }) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (version: number) => {
      const accepted = await acceptConsent(version);
      client.setQueryData(consentKey, accepted);
      return accepted;
    },
    onSuccess: () => {
      on.onAccepted();
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });
}

/** The consent text read afresh, outside the cache: the person agrees to the current text. */
export function useFetchConsent() {
  return (signal: AbortSignal) => getConsent(signal);
}

/**
 * Records the consent that resumes the waiting generations of `documentId`'s capture: the answer
 * goes to the cache, then `onSuccess` runs, then the base CVs and the document are read again;
 * the mutation ends once both are read.
 */
export function useResumeConsent(
  documentId: string,
  handlers: Pick<UseMutationOptions<Consent, Error, number>, "onSuccess">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => acceptConsent(version),
    onSuccess: async (...args) => {
      await client.cancelQueries({ queryKey: consentKey });
      client.setQueryData(consentKey, args[0]);
      await handlers.onSuccess?.(...args);
      await Promise.all([
        client.invalidateQueries({ queryKey: baseCvsKey }),
        client.invalidateQueries({ queryKey: documentKey(documentId) }),
      ]);
    },
  });
}
