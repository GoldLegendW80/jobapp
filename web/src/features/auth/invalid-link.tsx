import { Link2Off } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useTitle } from "@/lib/use-title";

import { AuthLayout } from "./auth-layout";

/** One page for every dead link: unknown, malformed, used, revoked or expired alike. */
export function InvalidLink() {
  useTitle("Invalid link");
  return (
    <AuthLayout>
      <Card>
        <CardHeader className="justify-items-center text-center">
          <Link2Off
            aria-hidden="true"
            className="size-8 text-muted-foreground"
          />
          <CardTitle>
            <h1 className="text-xl">This link is invalid or expired</h1>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-center text-sm text-muted-foreground">
          <p>
            A link works once. An invite lasts 7 days, a password reset 1 hour.
          </p>
          <p>Ask the admin for a new link.</p>
        </CardContent>
        <CardFooter className="justify-center">
          <Button asChild variant="outline">
            <Link to="/login">Go to sign in</Link>
          </Button>
        </CardFooter>
      </Card>
    </AuthLayout>
  );
}
