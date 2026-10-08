import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { useTitle } from "@/lib/use-title";

export function NotFoundPage() {
  useTitle("Page not found");
  return (
    <section
      aria-labelledby="not-found-title"
      className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center"
    >
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1
        id="not-found-title"
        className="text-2xl font-semibold tracking-tight"
      >
        Page not found
      </h1>
      <p className="text-muted-foreground">
        This page does not exist, or you do not have access to it.
      </p>
      <Button asChild variant="outline">
        <Link to="/">Go to the home page</Link>
      </Button>
    </section>
  );
}
