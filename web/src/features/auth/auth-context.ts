import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";

import { getMe, type User } from "./api";

/** The one query that says who is signed in: a User, or null for nobody. */
export const meQueryKey = ["me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: ({ signal }) => getMe(signal),
    staleTime: 60_000,
  });
}

export interface AuthActions {
  /** After a successful sign in or link: drop the previous user's cache, remember this one. */
  signIn: (user: User) => void;
  /** Ends the session on the server, then forgets everything locally. */
  signOut: () => void;
  signingOut: boolean;
  /** True right after an explicit sign out, so the guard lands on /login without `next`. */
  wasSignedOut: () => boolean;
  clearSignedOut: () => void;
}

export const AuthContext = createContext<AuthActions | null>(null);

export function useAuth(): AuthActions {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
