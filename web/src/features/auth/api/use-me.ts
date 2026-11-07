import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import { useMemo } from "react";

import { changePassword, getMe, renameMe, type User } from "./auth-api";

/** The one query that says who is signed in: a User, or null for nobody. */
export const meQueryKey = ["me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => getMe(signal),
    staleTime: 60_000,
  });
}

/** Forget every cached answer except who is signed in, which becomes `user`. */
export function resetSession(client: QueryClient, user: User | null) {
  void client.cancelQueries({ queryKey: meQueryKey });
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });
  client.setQueryData(meQueryKey, user);
}

/**
 * The session's side of the cache, the same across renders: `reset` (resetSession), and
 * `hadUser`, whether the cache holds a signed in user.
 */
export function useSessionCache() {
  const client = useQueryClient();
  return useMemo(
    () => ({
      reset: (user: User | null) => {
        resetSession(client, user);
      },
      hadUser: () => Boolean(client.getQueryData<User | null>(meQueryKey)),
    }),
    [client],
  );
}

/** Rename the signed in user (the name, cleaned); the user it answers becomes the cached one. */
export function useRenameMe(
  handlers: Pick<
    UseMutationOptions<User, Error, string>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => renameMe(name),
    ...handlers,
    onSuccess: (saved, ...rest) => {
      client.setQueryData(meQueryKey, saved);
      return handlers.onSuccess?.(saved, ...rest);
    },
  });
}

/** Change the signed in user's password: the api ends every other session and keeps this one. */
export function useChangePassword(
  handlers: Pick<
    UseMutationOptions<
      void,
      Error,
      { current_password: string; new_password: string }
    >,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  return useMutation({ mutationFn: changePassword, ...handlers });
}
