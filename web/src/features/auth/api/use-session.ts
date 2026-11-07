import {
  useMutation,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { login, logout, type User } from "./auth-api";
import { resetSession } from "./use-me";

/** Sign in with an email address (trimmed) and a password; the caller keeps the user. */
export function useLogin(
  handlers: Pick<
    UseMutationOptions<User, Error, { email: string; password: string }>,
    "onSuccess" | "onError"
  >,
) {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      login(email.trim(), password),
    ...handlers,
  });
}

/** Sign out: ends the session on the server, then forgets every cached answer. */
export function useLogout(
  handlers: Pick<UseMutationOptions<void>, "onSuccess" | "onError">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: logout,
    ...handlers,
    onSuccess: (...args) => {
      resetSession(client, null);
      return handlers.onSuccess?.(...args);
    },
  });
}
