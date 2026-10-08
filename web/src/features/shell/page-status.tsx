import { LoaderCircle, RotateCw, ServerCrash } from "lucide-react";

import { Button } from "@/components/ui/button";
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
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
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
      <main className="flex max-w-sm flex-col items-center gap-4 text-center">
        <ServerCrash
          aria-hidden="true"
          className="size-8 text-muted-foreground"
        />
        <h1 className="text-xl font-semibold tracking-tight">
          jobapp cannot reach its server
        </h1>
        <p role="alert" className="text-sm text-muted-foreground">
          {describeError(error)}
        </p>
        <Button
          variant="outline"
          onClick={onRetry}
          disabled={retrying}
          aria-busy={retrying}
        >
          <RotateCw
            aria-hidden="true"
            className={retrying ? "animate-spin" : undefined}
          />
          Retry
        </Button>
      </main>
    </div>
  );
}
