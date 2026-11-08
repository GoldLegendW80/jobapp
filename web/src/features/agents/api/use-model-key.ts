import { useQuery } from "@tanstack/react-query";

import { getModelCredential, modelCredentialQueryKey } from "./agents-api";

/** The saved model credential, or null: whether the wizard's Model access is done. Never retried. */
export function useModelKey() {
  return useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
    retry: false,
  });
}
