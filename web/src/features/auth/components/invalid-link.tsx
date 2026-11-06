import { Link2Off } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { useTitle } from "@/hooks/use-title";

import { AuthLayout } from "@/components/shared/auth/auth-layout";

/** One page for every dead link: unknown, malformed, used, revoked or expired alike. */
export function InvalidLink() {
  useTitle("Invalid link");
  return (
    <AuthLayout>
      <Card>
        <CardContent>
          <Empty className="gap-4 p-0 md:p-0">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Link2Off aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle className="text-xl font-semibold">
                <h1>This link is invalid or expired</h1>
              </EmptyTitle>
              <EmptyDescription>
                <p>
                  A link works once. An invite lasts 7 days, a password reset 1
                  hour.
                </p>
                <p>Ask the admin for a new link.</p>
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button asChild variant="outline">
                <Link to="/login">Go to sign in</Link>
              </Button>
            </EmptyContent>
          </Empty>
        </CardContent>
      </Card>
    </AuthLayout>
  );
}
