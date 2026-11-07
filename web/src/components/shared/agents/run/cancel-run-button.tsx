import { Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { useCancelRun } from "@/features/agents/api/use-cancel-run";

/** Cancel of a queued or running run; it says Stopping once asked, until the run ends. */
export function CancelRunButton({
  runId,
  stopping,
  size = "default",
  onFocus,
  onBlur,
}: {
  runId: string;
  /** The run was asked to stop and has not ended yet. */
  stopping: boolean;
  size?: "default" | "sm";
  onFocus?: () => void;
  onBlur?: () => void;
}) {
  const cancel = useCancelRun(runId);
  return (
    <Button
      variant="outline"
      size={size}
      onClick={() => {
        cancel.mutate();
      }}
      onFocus={onFocus}
      onBlur={onBlur}
      disabled={cancel.isPending || stopping}
      aria-busy={cancel.isPending}
    >
      {cancel.isPending || stopping ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : (
        <Square aria-hidden="true" />
      )}
      {stopping ? "Stopping" : "Cancel"}
    </Button>
  );
}
