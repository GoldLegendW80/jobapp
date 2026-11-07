import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";

import type { RunStatus } from "@/features/agents/api/runs-api";
import { STATUS_LABEL } from "@/features/agents/helpers/format";

const VARIANT = {
  queued: "outline",
  running: "info",
  done: "success",
  failed: "destructive",
  cancelled: "outline",
} as const satisfies Record<RunStatus, string>;

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge variant={VARIANT[status]} data-status={status}>
      {status === "running" ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : null}
      {STATUS_LABEL[status]}
    </Badge>
  );
}
