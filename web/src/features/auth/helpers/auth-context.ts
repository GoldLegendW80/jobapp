import { createContext, useContext } from "react";

import type { User } from "../api/auth-api";

export interface AuthActions {
  /** After a successful sign in or link: drop the previous user's cache, remember this one. */
  signIn: (user: User) => void;
  /** Ends the session on the server, then forgets everything locally. */
  signOut: () => void;
  signingOut: boolean;
  /** True right after an explicit sign out, so the guard lands on /login without `next`. */
  wasSignedOut: () => boolean;
  /** True once a signed in browser lost its session (a 401 on a call): the sign in page says so. */
  wasSessionEnded: () => boolean;
  clearSignedOut: () => void;
}

export const AuthContext = createContext<AuthActions | null>(null);

export function useAuth(): AuthActions {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
