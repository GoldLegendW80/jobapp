import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";

import type { RunStatus } from "@/features/agents/api/runs-api";
import { STATUS_LABEL } from "@/features/agents/helpers/format";

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const variant =
    status === "failed"
      ? "destructive"
      : status === "done"
        ? "default"
        : status === "running"
          ? "secondary"
          : "outline";
  return (
    <Badge variant={variant} data-status={status}>
      {status === "running" ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : null}
      {STATUS_LABEL[status]}
    </Badge>
  );
}
