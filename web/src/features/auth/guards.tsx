import type { ReactNode } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router";

import { PageLoading, PageUnavailable } from "@/features/shell/page-status";
import { NotFoundPage } from "@/features/shell/not-found-page";

import { useAuth, useMe } from "./auth-context";
import { loginPath, safeNext } from "./next";

/** Signed in users only; anyone else goes to /login?next=<this page>. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  const { wasSignedOut } = useAuth();
  if (me.data) return children;
  if (me.isPending) return <PageLoading />;
  if (me.isError)
    return (
      <PageUnavailable
        error={me.error}
        onRetry={() => void me.refetch()}
        retrying={me.isFetching}
      />
    );
  return (
    <Navigate to={wasSignedOut() ? "/login" : loginPath(location)} replace />
  );
}

/** Admins only. Anyone else sees the not found page, as the api answers 404 to members. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe();
  if (me.data?.role !== "admin") return <NotFoundPage />;
  return children;
}

/** The sign in page: a signed in visitor goes straight to `next` (sanitised) or home. */
export function PublicOnly({ children }: { children: ReactNode }) {
  const me = useMe();
  const [params] = useSearchParams();
  if (me.data) return <Navigate to={safeNext(params.get("next"))} replace />;
  if (me.isPending) return <PageLoading />;
  // No session, or the check failed: the form itself reports a server that cannot be reached.
  return children;
}
