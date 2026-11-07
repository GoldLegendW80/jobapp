import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { meQueryKey } from "@/features/auth/api/use-me";
import { isApiError } from "@/lib/api";

import { invitesKey, updateUser, usersKey } from "./admin-api";
import { message } from "../helpers/errors";
import {
  confirmationWords,
  USER_CHANGE,
  type PendingChange,
} from "../helpers/user-actions";

/** The save of a confirmed change to an account; `onDone` closes the confirmation. */
export function useUserChange(onDone: () => void) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [error, setError] = useState<string>();
  const mutation = useMutation({
    mutationFn: ({ user, action }: PendingChange) =>
      updateUser(user.id, USER_CHANGE[action]),
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: async (_user, done) => {
      toast.success(confirmationWords(done).done);
      onDone();
      if (done.self && done.action === "demote") {
        // No longer an admin: leave the admin area and let /me drop the Admin entry.
        await navigate("/jobs", { replace: true });
        await client.refetchQueries({ queryKey: meQueryKey });
        client.removeQueries({ queryKey: usersKey });
        client.removeQueries({ queryKey: invitesKey });
        return;
      }
      await client.invalidateQueries({ queryKey: usersKey });
    },
    onError: (failure) => {
      void client.invalidateQueries({ queryKey: usersKey });
      if (isApiError(failure) && failure.status === 404) {
        toast.error("This account no longer exists.");
        onDone();
        return;
      }
      setError(message(failure));
    },
  });

  return {
    change: mutation.mutate,
    changing: mutation.isPending,
    error,
    clearError: () => {
      setError(undefined);
    },
  };
}
