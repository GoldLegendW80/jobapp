import { useId } from "react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

import {
  FACTS_MAX_BYTES,
  FACTS_WARN_SHARE,
  sizeText,
} from "@/features/profile/helpers/fact-base";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/**
 * How big the fact base is against the api's cap, as the fact parts are typed: amber from 90%,
 * red over the cap, with what to do then.
 */
export function FactSizeMeter({ bytes }: { bytes: number }) {
  const labelId = useId();
  const percent = Math.min(100, Math.round((100 * bytes) / FACTS_MAX_BYTES));
  const over = bytes > FACTS_MAX_BYTES;
  const near = !over && bytes >= FACTS_MAX_BYTES * FACTS_WARN_SHARE;
  const text = sizeText(bytes, FACTS_MAX_BYTES);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs">
        <span id={labelId} className="font-medium">
          {W.size}
        </span>
        <span
          className={cn(
            "text-muted-foreground tabular-nums",
            near && "font-medium text-warning",
            over && "font-medium text-destructive",
          )}
        >
          {text}
        </span>
      </div>
      {/* A meter, not a progress: the bar's value is the share filled, what is read the bytes. */}
      <Progress
        value={percent}
        role="meter"
        aria-labelledby={labelId}
        aria-valuemin={0}
        aria-valuemax={FACTS_MAX_BYTES}
        aria-valuenow={Math.min(bytes, FACTS_MAX_BYTES)}
        aria-valuetext={text}
        className={cn(
          "h-1.5 bg-muted *:data-[slot=progress-indicator]:motion-reduce:transition-none",
          over
            ? "*:data-[slot=progress-indicator]:bg-destructive"
            : near
              ? "*:data-[slot=progress-indicator]:bg-warning"
              : undefined,
        )}
      />
      {over ? (
        <p role="alert" className="text-xs text-destructive">
          {W.overSize}
        </p>
      ) : null}
    </div>
  );
}
