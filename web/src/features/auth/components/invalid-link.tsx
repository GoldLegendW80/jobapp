import { Link2Off } from "lucide-react";
import { Link } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { AuthLayout } from "@/components/shared/auth/auth-layout";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";

/** One page for every dead link: unknown, malformed, used, revoked or expired alike. */
export function InvalidLink() {
  useTitle("Invalid link");
  return (
    <AuthLayout>
      <EmptyState
        icon={Link2Off}
        title={
          <h1 className="text-xl tracking-tight">
            This link is invalid or expired
          </h1>
        }
        description={
          <>
            <p>
              A link works once. An invite lasts 7 days, a password reset 1
              hour.
            </p>
            <p>Ask the admin for a new link.</p>
          </>
        }
        actions={
          <Button asChild>
            <Link to="/login">Go to sign in</Link>
          </Button>
        }
      />
    </AuthLayout>
  );
}
