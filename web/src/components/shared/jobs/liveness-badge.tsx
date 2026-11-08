import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { Liveness } from "@/features/jobs/api/liveness-api";
import {
  MAY_BE_CLOSED,
  mayBeClosed,
} from "@/features/jobs/helpers/liveness-words";

/**
 * "May be closed": the last check of the posting's own address found that its page may say so,
 * and the posting is still open. Shown on the posting's header and on its Discover card; nothing
 * otherwise.
 */
export function LivenessBadge({
  liveness,
  status,
  className,
}: {
  liveness?: Liveness | null;
  /** The posting's status; a Discover card has none (every card is still found). */
  status?: string;
  className?: string;
}) {
  if (!mayBeClosed({ liveness, status })) return null;
  return (
    <Badge
      variant="warning"
      data-testid="liveness-badge"
      className={cn("align-middle font-normal", className)}
    >
      {MAY_BE_CLOSED}
    </Badge>
  );
}
