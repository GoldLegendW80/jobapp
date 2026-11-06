import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import {
  FIT_TONE_CLASSES,
  fitTone,
  statusLabel,
  statusTone,
} from "@/features/jobs/helpers/labels";
import { payWords, type PayFields } from "@/features/jobs/helpers/pay-words";

export function StatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  return (
    <Badge
      variant="outline"
      data-status={status}
      className={cn(statusTone(status), className)}
    >
      {statusLabel(status)}
    </Badge>
  );
}

/**
 * A hint shown when the pointer rests on a badge, which is not a control. The span anchors the
 * tooltip: a Badge drops its ref on React 18.
 */
export function BadgeHint({
  hint,
  children,
}: {
  hint: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{children}</span>
      </TooltipTrigger>
      <TooltipContent className="motion-reduce:animate-none!">
        {hint}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The fit score on the colour scale of the histogram, said in words too ("Fit 13"), so the colour
 * is never the only clue; "Unscored" when there is none.
 */
export function FitBadge({
  fit,
  overridden = false,
  className,
}: {
  fit: number | null;
  overridden?: boolean;
  className?: string;
}) {
  const tone = fitTone(fit);
  const badge = (
    <Badge
      variant="outline"
      data-tone={tone}
      aria-label={
        fit === null
          ? "Unscored"
          : `Fit ${fit} out of 100${overridden ? ", set by hand" : ""}`
      }
      className={cn(
        "tabular-nums",
        FIT_TONE_CLASSES[tone],
        fit === null && "font-normal",
        className,
      )}
    >
      {fit === null ? (
        "Unscored"
      ) : overridden ? (
        <>
          {`Fit ${fit}`}
          <span className="font-normal opacity-80">manual</span>
        </>
      ) : (
        `Fit ${fit}`
      )}
    </Badge>
  );
  return overridden ? (
    <BadgeHint hint="Fit set by hand">{badge}</BadgeHint>
  ) : (
    badge
  );
}

/**
 * The pay a posting states, in words with separators ("45,000 to 55,000 EUR a year"), or "Pay not
 * given" in a muted style when it states none: never a guess.
 */
export function PayText({
  job,
  className,
}: {
  job: PayFields;
  className?: string;
}) {
  const pay = payWords(job);
  return pay ? (
    <span data-pay="stated" className={cn("tabular-nums", className)}>
      {pay}
    </span>
  ) : (
    <span
      data-pay="none"
      className={cn(className, "font-normal text-muted-foreground")}
    >
      {WORDING.payNotGiven}
    </span>
  );
}
