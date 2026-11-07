import { RotateCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { useHealth } from "../api/use-health";

/**
 * The api health check, in the footer. While the server answers nothing shows (a screen reader
 * still reads "Server reachable"); when it does not, a red dot, "Server unreachable" with the
 * reason for screen readers and on hover, and a button to check again.
 */
export function HealthDot() {
  const health = useHealth();
  const checking = health.isFetching;
  // The reason's tooltip opens on hover only while there is a reason to show.
  const [hover, setHover] = useState(false);
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
      {/* Always the same live region (a tooltip that came and went would remount it), its
          tooltip held shut while there is no reason. */}
      <Tooltip open={hover && reason !== undefined} onOpenChange={setHover}>
        <TooltipTrigger asChild>
          <p
            role="status"
            aria-live="polite"
            className={cn(
              "flex min-w-0 items-center gap-1.5",
              !shown && "sr-only",
            )}
          >
            {shown ? (
              <span
                aria-hidden="true"
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  state === "down" && "bg-destructive",
                  state === "checking" &&
                    // eslint-disable-next-line no-restricted-syntax -- a status light that pulses while checking, inside a <p>: not a placeholder, and Skeleton is a <div>.
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
        </TooltipTrigger>
        <TooltipContent>{reason}</TooltipContent>
      </Tooltip>
      {shown ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={WORDING.checkServerAgain}
              onClick={() => void health.refetch()}
              disabled={checking}
              aria-busy={checking}
              className="hover:text-foreground"
            >
              {checking ? (
                <Spinner
                  aria-hidden="true"
                  role={undefined}
                  className="size-3 motion-reduce:animate-none"
                />
              ) : (
                <RotateCw aria-hidden="true" className="size-3" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{WORDING.checkServerAgain}</TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  );
}
