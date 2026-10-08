import {
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from "react";
import { toast } from "sonner";

import { describeError, onUnauthorized } from "@/lib/api";

import { logout, type User } from "./api";
import { AuthContext, meQueryKey, type AuthActions } from "./auth-context";

/** Forget every cached answer except who is signed in, which becomes `user`. */
function resetSession(client: QueryClient, user: User | null) {
  void client.cancelQueries({ queryKey: meQueryKey });
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });
  client.setQueryData(meQueryKey, user);
}

/**
 * Session state for the whole app. A 401 from any api call (the session expired, was ended on
 * another device or by the admin) signs the browser out locally; the protected routes then send
 * it to /login?next=<this page>. Public pages are untouched: they render the same with no user.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const signedOut = useRef(false);

  useEffect(
    () =>
      onUnauthorized(() => {
        signedOut.current = false;
        resetSession(client, null);
      }),
    [client],
  );

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      signedOut.current = true;
      resetSession(client, null);
    },
    onError: (error) => {
      toast.error(`Sign out failed. ${describeError(error)} Try again.`);
    },
  });
  const { mutate: runLogout, isPending: signingOut } = logoutMutation;

  const signIn = useCallback(
    (user: User) => {
      signedOut.current = false;
      resetSession(client, user);
    },
    [client],
  );
  const signOut = useCallback(() => {
    runLogout();
  }, [runLogout]);
  const wasSignedOut = useCallback(() => signedOut.current, []);
  const clearSignedOut = useCallback(() => {
    signedOut.current = false;
  }, []);

  const value = useMemo<AuthActions>(
    () => ({ signIn, signOut, signingOut, wasSignedOut, clearSignedOut }),
    [signIn, signOut, signingOut, wasSignedOut, clearSignedOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
