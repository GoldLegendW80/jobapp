import { RotateCw } from "lucide-react";

import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useHealth } from "./use-health";

/**
 * The api health check, small, in the footer: a green dot and "API ok" when the backend and its
 * database answer, red with the reason otherwise, and a button to check again.
 */
export function HealthDot() {
  const health = useHealth();
  const checking = health.isFetching;
  const state =
    health.isPending || (checking && health.isError)
      ? "checking"
      : health.isError
        ? "down"
        : "ok";
  return (
    <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
      <p
        role="status"
        aria-live="polite"
        className="flex min-w-0 items-center gap-1.5"
      >
        <span
          aria-hidden="true"
          className={cn(
            "size-2 shrink-0 rounded-full",
            state === "ok" && "bg-success",
            state === "down" && "bg-destructive",
            state === "checking" && "animate-pulse bg-muted-foreground",
          )}
        />
        {state === "checking" ? (
          "Checking"
        ) : state === "down" ? (
          <>
            <span className="shrink-0">API unavailable</span>
            <span className="truncate">{describeError(health.error)}</span>
          </>
        ) : (
          "API ok"
        )}
      </p>
      <button
        type="button"
        aria-label="Check the API again"
        title="Check the API again"
        onClick={() => void health.refetch()}
        disabled={checking}
        aria-busy={checking}
        className="grid size-6 shrink-0 place-items-center rounded-md hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
      >
        <RotateCw
          aria-hidden="true"
          className={cn("size-3", checking && "animate-spin")}
        />
      </button>
    </div>
  );
}
