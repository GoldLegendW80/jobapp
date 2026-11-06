import { useQuery } from "@tanstack/react-query";

import { getModelCredential, modelCredentialQueryKey } from "./agents-api";

/** The person's saved model credential, or null when none is saved. */
export function useModelCredential() {
  return useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
  });
}
