import { CircleAlert, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

/**
 * In place of something that could not be loaded: what failed, why, and Retry. Takes the query
 * itself, so the retry, its busy state and the reason always match it.
 */
export function LoadError({
  query,
  reason,
  children,
}: {
  query: { error: unknown; isFetching: boolean; refetch: () => unknown };
  /** Why, when the page must not show the server's own words (they may name its internals). */
  reason?: ReactNode;
  /** What could not be loaded, as a sentence: "The postings could not be loaded." */
  children: ReactNode;
}) {
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertDescription className="flex flex-col items-start gap-3">
        <p>
          {children} {reason ?? describeError(query.error)}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          aria-busy={query.isFetching}
        >
          {query.isFetching ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <RotateCw aria-hidden="true" />
          )}
          Retry
        </Button>
      </AlertDescription>
    </Alert>
  );
}
