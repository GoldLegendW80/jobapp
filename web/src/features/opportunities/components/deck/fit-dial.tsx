import { fitTone, type FitTone } from "@/features/jobs/helpers/labels";
import { cn } from "@/lib/utils";

const RING: Record<FitTone, string> = {
  top: "stroke-success",
  good: "stroke-success",
  fair: "stroke-warning",
  low: "stroke-destructive",
  none: "stroke-transparent",
};

const FIGURE: Record<FitTone, string> = {
  top: "text-success",
  good: "text-success",
  fair: "text-warning",
  low: "text-destructive",
  none: "text-muted-foreground",
};

const R = 26;
const LENGTH = 2 * Math.PI * R;

/**
 * The fit of a deck card as its hero figure: the number large, inside a ring filled to it, in the
 * colour of the fit scale (the colour is never the only clue: the number says it).
 */
export function FitDial({
  fit,
  className,
}: {
  fit: number | null;
  className?: string;
}) {
  const tone = fitTone(fit);
  const filled = fit === null ? 0 : Math.max(0, Math.min(100, fit)) / 100;
  return (
    <div
      role="img"
      aria-label={fit === null ? "Unscored" : `Fit ${fit} out of 100`}
      className={cn(
        "relative grid size-18 shrink-0 place-items-center",
        className,
      )}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 64 64"
        className="absolute inset-0 size-full -rotate-90"
      >
        <circle
          cx="32"
          cy="32"
          r={R}
          fill="none"
          strokeWidth="6"
          className="stroke-foreground/10"
        />
        <circle
          cx="32"
          cy="32"
          r={R}
          fill="none"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={`${(LENGTH * filled).toFixed(2)} ${LENGTH.toFixed(2)}`}
          className={cn(
            RING[tone],
            "transition-[stroke-dasharray] duration-500 ease-out motion-reduce:transition-none",
          )}
        />
      </svg>
      <span
        aria-hidden="true"
        className={cn(
          "flex flex-col items-center leading-none tabular-nums",
          FIGURE[tone],
        )}
      >
        <span
          className={cn("font-semibold", fit === null ? "text-sm" : "text-2xl")}
        >
          {fit === null ? "None" : fit}
        </span>
        <span className="mt-0.5 text-[0.625rem] font-medium text-muted-foreground">
          fit
        </span>
      </span>
    </div>
  );
}
