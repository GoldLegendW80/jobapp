import { Button } from "@/components/ui/button";
import type { JobDetail } from "@/features/jobs/api/jobs-api";

import type { Approval } from "@/features/apply/api/apply-api";
import { blockingFields } from "@/features/apply/helpers/apply-state";

/**
 * Approve and submit (it opens the confirmation) and Reject, for a filled form waiting for the
 * person. Approve waits while an answer is typed and not saved, or a required one is left.
 */
export function ReviewButtons({
  job,
  approval,
  busy,
  unsaved,
  onApprove,
  onReject,
}: {
  job: JobDetail;
  approval: Approval;
  busy: boolean;
  unsaved: number;
  onApprove: () => void;
  onReject: () => void;
}) {
  const blocking = blockingFields(approval.answers);
  return (
    <>
      <div className="flex w-full flex-col gap-1 @sm:w-fit">
        <Button
          disabled={busy || blocking.length > 0 || unsaved > 0}
          onClick={onApprove}
        >
          Approve and submit
        </Button>
        {unsaved > 0 ? (
          <p className="text-sm text-warning">
            Save your answers first: only saved answers are sent.
          </p>
        ) : blocking.length > 0 ? (
          <p className="text-sm text-warning">
            Answer {blocking.map((answer) => answer.label).join(", ")} first:
            the form cannot go without it.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Sends your application to {job.company}.
          </p>
        )}
      </div>
      <Button
        variant="outline"
        className="w-full @sm:w-fit"
        disabled={busy}
        onClick={onReject}
      >
        Reject
      </Button>
    </>
  );
}
