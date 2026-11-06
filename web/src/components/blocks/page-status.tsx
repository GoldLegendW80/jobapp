import { RotateCw, ServerCrash } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

/**
 * Shown while the session is being checked. The spinner fades in only after a short delay, so a
 * fast answer never flashes a loading screen before the page.
 */
export function PageLoading({ label = "Loading" }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="grid min-h-dvh place-items-center px-4"
    >
      <span className="delayed-appear flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner aria-hidden="true" role={undefined} />
        {label}
      </span>
    </div>
  );
}

/** The session check itself failed (server down, timeout): say so and offer a retry. */
export function PageUnavailable({
  error,
  onRetry,
  retrying,
}: {
  error: unknown;
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <main className="w-full max-w-sm">
        <Empty className="gap-4 p-0 md:p-0">
          <EmptyHeader className="gap-4">
            <EmptyMedia className="mb-0">
              <ServerCrash
                aria-hidden="true"
                className="size-8 text-muted-foreground"
              />
            </EmptyMedia>
            <EmptyTitle className="text-xl font-semibold">
              <h1>jobapp cannot reach its server</h1>
            </EmptyTitle>
            <EmptyDescription role="alert">
              {describeError(error)}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              variant="outline"
              onClick={onRetry}
              disabled={retrying}
              aria-busy={retrying}
            >
              {retrying ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : (
                <RotateCw aria-hidden="true" />
              )}
              Retry
            </Button>
          </EmptyContent>
        </Empty>
      </main>
    </div>
  );
}
