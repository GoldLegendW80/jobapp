import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getModelCredential,
  modelCredentialQueryKey,
  saveModelCredential,
  verifyModelCredential,
  type VerifyResult,
} from "./agents-api";

/** The saved model credential for the welcome summary's key form: read once, never retried. */
export function useModelKey() {
  return useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
    retry: false,
  });
}

/** Saves a pasted key; the credential the api answers goes to the cache. */
export function useSaveModelKey(on: {
  onStart: () => void;
  onSaved: () => void;
  onError: (error: Error) => void;
}) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveModelCredential,
    // The variables hold the key: the cache forgets the mutation once nothing observes it.
    gcTime: 0,
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (value) => {
      client.setQueryData(modelCredentialQueryKey, value);
      on.onSaved();
    },
    onError: on.onError,
  });
}

/** Verify: the credential the api answers goes to the cache; a refusal reads it again. */
export function useVerifyModelKey(on: {
  onStart: () => void;
  onVerified: (result: VerifyResult) => void;
  onError: (error: Error) => void;
}) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: verifyModelCredential,
    onMutate: () => {
      on.onStart();
    },
    onSuccess: (result) => {
      client.setQueryData(modelCredentialQueryKey, result.credential);
      on.onVerified(result);
    },
    onError: (error) => {
      on.onError(error);
      // A refused key is no longer verified: read what the server keeps.
      void client.invalidateQueries({ queryKey: modelCredentialQueryKey });
    },
  });
}
