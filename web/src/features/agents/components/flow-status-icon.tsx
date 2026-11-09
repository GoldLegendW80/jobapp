import {
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CircleStop,
  CircleX,
  type LucideIcon,
} from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import type { FlowStatus } from "../api/activity-api";
import { FLOW_STATUS_WORDS } from "../helpers/activity";

const ICONS: Record<FlowStatus, { icon: LucideIcon; className: string }> = {
  waiting: { icon: CircleDashed, className: "text-muted-foreground/60" },
  queued: { icon: CircleDashed, className: "text-muted-foreground" },
  running: { icon: CircleDashed, className: "text-warning" },
  success: { icon: CircleCheck, className: "text-success" },
  failure: { icon: CircleX, className: "text-destructive" },
  skipped: { icon: CircleSlash, className: "text-muted-foreground" },
  cancelled: { icon: CircleStop, className: "text-muted-foreground" },
};

/**
 * A run's or a step's status as an icon, its words for a screen reader. Running turns, still
 * under reduced motion, where its colour and words still say it.
 */
export function FlowStatusIcon({
  status,
  className,
}: {
  status: FlowStatus;
  className?: string;
}) {
  const { icon: Icon, className: tone } = ICONS[status];
  return (
    <>
      {status === "running" ? (
        <Spinner
          aria-hidden="true"
          role={undefined}
          data-status={status}
          className={cn(
            "size-4 shrink-0 motion-reduce:animate-none",
            tone,
            className,
          )}
        />
      ) : (
        <Icon
          aria-hidden="true"
          data-status={status}
          className={cn("size-4 shrink-0", tone, className)}
        />
      )}
      <span className="sr-only">{FLOW_STATUS_WORDS[status]}, </span>
    </>
  );
}
