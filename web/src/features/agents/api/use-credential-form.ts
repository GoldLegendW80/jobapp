/** The form of the Model access card: the kind chosen, the secret pasted, and its save. */
import { useMutation } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { fieldError } from "@/features/account/helpers/field-error";
import { message } from "@/features/admin/helpers/errors";
import {
  throttledFor,
  useRetryCountdown,
} from "@/features/auth/helpers/retry-countdown";

import {
  saveModelCredential,
  type CredentialKind,
  type ModelCredential,
} from "./agents-api";
import { EMPTY_SECRET, SAVED } from "../helpers/model-credential";

export function useCredentialForm({
  saved,
  onSaving,
  onSaved,
}: {
  saved: ModelCredential | null;
  onSaving: () => void;
  onSaved: (value: ModelCredential) => void;
}) {
  const secretId = useId();
  const [kind, setKind] = useState<CredentialKind | undefined>();
  const chosen: CredentialKind = kind ?? saved?.kind ?? "anthropic_api_key";
  const [secret, setSecret] = useState("");
  const [shown, setShown] = useState(false);
  const [secretError, setSecretError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const saveWait = useRetryCountdown(() => {
    setFormError(undefined);
  });

  const save = useMutation({
    mutationFn: saveModelCredential,
    // The variables hold the secret: once the mutation has no observer it leaves the cache at
    // once instead of after the default 5 minutes.
    gcTime: 0,
    onMutate: () => {
      setFormError(undefined);
      setSecretError(undefined);
      onSaving();
    },
    onSuccess: (value) => {
      onSaved(value);
      setSecret("");
      setShown(false);
      setKind(undefined);
      toast.success(SAVED);
      // Drop the mutation (its variables are the secret) instead of keeping it until the next
      // save; with gcTime 0 the cache forgets it too.
      save.reset();
    },
    onError: (error) => {
      const wait = throttledFor(error);
      if (wait !== undefined) saveWait.start(wait);
      const found = fieldError(error, ["secret"] as const);
      if (found) {
        setSecretError(found.message);
        document.getElementById(secretId)?.focus();
        return;
      }
      setFormError(message(error));
    },
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (secret.trim() === "") {
      setSecretError(EMPTY_SECRET);
      document.getElementById(secretId)?.focus();
      return;
    }
    save.mutate({ kind: chosen, secret });
  };

  return {
    secretId,
    chosen,
    choose: setKind,
    secret,
    changeSecret: (value: string) => {
      setSecret(value);
      setSecretError(undefined);
    },
    shown,
    toggleShown: () => {
      setShown((value) => !value);
    },
    secretError,
    formError,
    save,
    saveWait,
    submit,
  };
}
