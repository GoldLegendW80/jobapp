import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import {
  exactTime,
  relativeTime,
} from "@/features/admin/helpers/relative-time";

/** A time said relatively ("2 days ago"), its exact date and hour in a tooltip. */
export function Moment({
  at,
  now,
  className,
}: {
  at: string;
  /** The page's clock, so every time of a table moves together; now when absent. */
  now?: Date;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time dateTime={at} className={className}>
          {relativeTime(at, now)}
        </time>
      </TooltipTrigger>
      <TooltipContent>{exactTime(at)}</TooltipContent>
    </Tooltip>
  );
}
