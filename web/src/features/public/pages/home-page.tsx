import { Link, Navigate } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useMe } from "@/features/auth/api/use-me";
import { PageLoading } from "@/components/blocks/page-status";
import { APP_NAME } from "@/hooks/use-title";

import { PublicLayout } from "@/components/shared/public/public-layout";
import { HOME_INTRO, HOME_INVITE } from "../helpers/texts";

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
      <PageHeader title={APP_NAME} description={HOME_INTRO} />
      <Card>
        <CardContent className="flex flex-col items-start gap-4">
          <p>{HOME_INVITE}</p>
          <Button asChild>
            <Link to="/login">Sign in</Link>
          </Button>
        </CardContent>
      </Card>
    </PublicLayout>
  );
}
