import { Link, Navigate } from "react-router";

import { Button } from "@/components/ui/button";
import { useMe } from "@/features/auth/auth-context";
import { PageLoading } from "@/features/shell/page-status";
import { APP_NAME } from "@/lib/use-title";

import { PublicLayout } from "./public-layout";
import { HOME_INTRO, HOME_INVITE } from "./texts";

/**
 * `/`: a signed in visitor goes to the jobs dashboard, anyone else reads the public home page.
 * The one session check this page makes may fail (the api is down): the page then shows as for a
 * visitor with no session.
 */
export function HomePage() {
  const me = useMe();
  if (me.data) return <Navigate to="/jobs" replace />;
  if (me.isPending) return <PageLoading />;
  return (
    <PublicLayout title="">
      <h1 className="text-2xl font-semibold tracking-tight">{APP_NAME}</h1>
      <p>{HOME_INTRO}</p>
      <p>{HOME_INVITE}</p>
      <div>
        <Button asChild>
          <Link to="/login">Sign in</Link>
        </Button>
      </div>
    </PublicLayout>
  );
}
