import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { useTitle } from "@/lib/use-title";

export function NotFoundPage() {
  useTitle("Page not found");
  return (
    <section aria-labelledby="not-found-title" className="mx-auto max-w-md">
      <Empty className="gap-4 px-0 py-16 md:px-0 md:py-16">
        <EmptyHeader className="max-w-md gap-4">
          <p className="text-sm font-medium text-muted-foreground">404</p>
          <EmptyTitle className="text-2xl font-semibold">
            <h1 id="not-found-title">Page not found</h1>
          </EmptyTitle>
          <EmptyDescription className="text-base/normal">
            This page does not exist, or you do not have access to it.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild variant="outline">
            <Link to="/">Go to the home page</Link>
          </Button>
        </EmptyContent>
      </Empty>
    </section>
  );
}
