/** The saved model credential, and what the Model access card does with it: verify, remove. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { message } from "@/features/admin/helpers/errors";
import {
  throttledFor,
  useRetryCountdown,
} from "@/features/auth/helpers/retry-countdown";

import {
  getModelCredential,
  modelCredentialQueryKey,
  removeModelCredential,
  verifyModelCredential,
  type ModelCredential,
} from "./agents-api";
import { REMOVED } from "../helpers/model-credential";

/** What the last Verify said. */
export type VerifyOutcome =
  { tone: "success" | "info"; text: string } | { tone: "error"; text: string };

export function useModelAccess() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
  });
  const saved = query.data ?? null;
  const setSaved = (value: ModelCredential | null) => {
    client.setQueryData(modelCredentialQueryKey, value);
  };
  const [outcome, setOutcome] = useState<VerifyOutcome>();
  const [confirming, setConfirming] = useState(false);
  const verifyWait = useRetryCountdown();

  const verify = useMutation({
    mutationFn: verifyModelCredential,
    onMutate: () => {
      setOutcome(undefined);
    },
    onSuccess: (result) => {
      setSaved(result.credential);
      setOutcome({
        tone: result.result === "verified" ? "success" : "info",
        text: result.message,
      });
    },
    onError: (error) => {
      const wait = throttledFor(error);
      if (wait !== undefined) verifyWait.start(wait);
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
    verifyWait,
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
