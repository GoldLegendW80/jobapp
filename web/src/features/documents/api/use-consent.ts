import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { acceptConsent, consentKey, getConsent } from "./spend-api";

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
