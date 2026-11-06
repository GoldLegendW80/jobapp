import { useCallback, useEffect, useMemo, useRef, type ReactNode } from "react";
import { toast } from "sonner";

import { describeError, onUnauthorized } from "@/lib/api";

import type { User } from "../api/auth-api";
import { useSessionCache } from "../api/use-me";
import { useLogout } from "../api/use-session";
import { AuthContext, type AuthActions } from "../helpers/auth-context";

/**
 * Session state for the whole app. A 401 from any api call (the session expired, was ended on
 * another device or by the admin) signs the browser out locally; the protected routes then send
 * it to /login?next=<this page>. Public pages are untouched: they render the same with no user.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const session = useSessionCache();
  const signedOut = useRef(false);
  const sessionEnded = useRef(false);

  useEffect(
    () =>
      onUnauthorized(() => {
        signedOut.current = false;
        // Only a browser that had a user lost a session; a first visit has nothing to lose.
        if (session.hadUser()) sessionEnded.current = true;
        session.reset(null);
      }),
    [session],
  );

  const logoutMutation = useLogout({
    onSuccess: () => {
      signedOut.current = true;
      sessionEnded.current = false;
    },
    onError: (error) => {
      toast.error(`Sign out failed. ${describeError(error)} Try again.`);
    },
  });
  const { mutate: runLogout, isPending: signingOut } = logoutMutation;

  const signIn = useCallback(
    (user: User) => {
      signedOut.current = false;
      sessionEnded.current = false;
      session.reset(user);
    },
    [session],
  );
  const signOut = useCallback(() => {
    runLogout();
  }, [runLogout]);
  const wasSignedOut = useCallback(() => signedOut.current, []);
  const wasSessionEnded = useCallback(() => sessionEnded.current, []);
  const clearSignedOut = useCallback(() => {
    signedOut.current = false;
  }, []);

  const value = useMemo<AuthActions>(
    () => ({
      signIn,
      signOut,
      signingOut,
      wasSignedOut,
      wasSessionEnded,
      clearSignedOut,
    }),
    [
      signIn,
      signOut,
      signingOut,
      wasSignedOut,
      wasSessionEnded,
      clearSignedOut,
    ],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
