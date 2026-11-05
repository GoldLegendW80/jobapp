import { RotateCw } from "lucide-react";

import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { useHealth } from "./use-health";

/**
 * The api health check, in the footer. While the server answers nothing shows (a screen reader
 * still reads "Server reachable"); when it does not, a red dot, "Server unreachable" with the
 * reason for screen readers and on hover, and a button to check again.
 */
export function HealthDot() {
  const health = useHealth();
  const checking = health.isFetching;
  // A retry after a failure puts the query back to pending (no data yet): the failure count
  // tells it from the first check, so the footer keeps saying the server is unreachable.
  const failedBefore = health.errorUpdateCount > 0;
  const state = health.isSuccess
    ? "ok"
    : !failedBefore
      ? "first"
      : checking
        ? "checking"
        : "down";
  const reason = state === "down" ? describeError(health.error) : undefined;
  const shown = state === "down" || state === "checking";
  return (
    <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
      <p
        role="status"
        aria-live="polite"
        title={reason}
        className={cn("flex min-w-0 items-center gap-1.5", !shown && "sr-only")}
      >
        {shown ? (
          <span
            aria-hidden="true"
            className={cn(
              "size-2 shrink-0 rounded-full",
              state === "down" && "bg-destructive",
              state === "checking" &&
                "animate-pulse bg-muted-foreground motion-reduce:animate-none",
            )}
          />
        ) : null}
        {state === "first" ? (
          "Checking the server"
        ) : state === "checking" ? (
          "Checking"
        ) : state === "down" ? (
          <>
            <span className="shrink-0 text-destructive">
              {WORDING.serverUnreachable}
            </span>
            <span className="sr-only">{reason}</span>
          </>
        ) : (
          "Server reachable"
        )}
      </p>
      {shown ? (
        <button
          type="button"
          aria-label={WORDING.checkServerAgain}
          title={WORDING.checkServerAgain}
          onClick={() => void health.refetch()}
          disabled={checking}
          aria-busy={checking}
          className="grid size-6 shrink-0 place-items-center rounded-md hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
        >
          <RotateCw
            aria-hidden="true"
            className={cn(
              "size-3",
              checking && "animate-spin motion-reduce:animate-none",
            )}
          />
        </button>
      ) : null}
    </div>
  );
}
