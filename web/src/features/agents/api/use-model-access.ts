/** The saved model credential, and what Model access does with it: verify, remove. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { message } from "@/features/admin/helpers/errors";

import {
  getModelCredential,
  modelCredentialQueryKey,
  removeModelCredential,
  verifyModelCredential,
  type ModelCredential,
} from "./agents-api";
import { REMOVED } from "../helpers/model-credential";

/** How often a credential whose check runs in the worker is read again. */
export const CHECK_POLL_MS = 2000;

/** What the last Verify said. */
export type VerifyOutcome =
  { tone: "success" | "info"; text: string } | { tone: "error"; text: string };

export function useModelAccess() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
    // A subscription token is checked by the worker: read again until the check has ended.
    refetchInterval: (current) =>
      current.state.data?.check?.state === "checking" ? CHECK_POLL_MS : false,
  });
  const saved = query.data ?? null;
  const setSaved = (value: ModelCredential | null) => {
    client.setQueryData(modelCredentialQueryKey, value);
  };
  const [outcome, setOutcome] = useState<VerifyOutcome>();
  const [confirming, setConfirming] = useState(false);

  const verify = useMutation({
    mutationFn: verifyModelCredential,
    onMutate: () => {
      setOutcome(undefined);
    },
    onSuccess: (result) => {
      setSaved(result.credential);
      // A check under way says itself in the credential's `check`.
      if (result.result !== "checking")
        setOutcome({
          tone: result.result === "verified" ? "success" : "info",
          text: result.message,
        });
    },
    onError: (error) => {
      setOutcome({ tone: "error", text: message(error) });
      // A refused key is no longer verified; the server cleared it.
      void client.invalidateQueries({ queryKey: modelCredentialQueryKey });
    },
  });

  const remove = useMutation({
    mutationFn: removeModelCredential,
    onSuccess: () => {
      setSaved(null);
      setOutcome(undefined);
      setConfirming(false);
      toast.success(REMOVED);
    },
  });

  return {
    query,
    saved,
    setSaved,
    outcome,
    clearOutcome: () => {
      setOutcome(undefined);
    },
    verify,
    remove,
    confirming,
    askToRemove: () => {
      remove.reset();
      setConfirming(true);
    },
    closeRemove: () => {
      setConfirming(false);
    },
  };
}
