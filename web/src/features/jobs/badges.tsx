import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { FIT_TONE_CLASSES, fitTone, statusLabel, statusTone } from "./labels";

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

/** The fit score on the colour scale of the histogram; "Unscored" when there is none. */
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
  return (
    <Badge
      variant="outline"
      data-tone={tone}
      title={overridden ? "Fit set by hand" : undefined}
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
      {fit === null ? "Unscored" : `${fit}${overridden ? "*" : ""}`}
    </Badge>
  );
}
