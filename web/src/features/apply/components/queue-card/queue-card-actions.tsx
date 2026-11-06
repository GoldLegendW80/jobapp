import { Link } from "react-router";

import { Button } from "@/components/ui/button";

import type { Approval } from "../../api/apply-api";
import type { QueueItem } from "../../api/queue-api";
import type { AfterClick } from "@/features/apply/api/use-queue-card-actions";
import { blockingFields } from "@/features/apply/helpers/apply-state";
import {
  canCancel,
  canConfirmSubmitted,
  canReject,
  canRetry,
  needsPostingPage,
} from "@/features/apply/helpers/queue-state";

/**
 * The buttons at the foot of a queue card, each where the item's state offers it: Approve and
 * submit (waiting on unsaved or required answers), the agent's list on the job page, Reject,
 * Cancel, "It was submitted", Try again and Open the posting.
 */
export function QueueCardActions({
  item,
  reviewable,
  agentList,
  approval,
  approvalFetching,
  busy,
  unsaved,
  onApprove,
  onReject,
  onCancel,
  onAsk,
}: {
  item: QueueItem;
  reviewable: boolean;
  agentList: boolean;
  approval: Approval | undefined;
  approvalFetching: boolean;
  busy: boolean;
  unsaved: number;
  onApprove: () => void;
  onReject: () => void;
  onCancel: () => void;
  onAsk: (kind: AfterClick) => void;
}) {
  const blocking = approval ? blockingFields(approval.answers) : [];
  return (
    <>
      {item.state === "ready" && agentList ? (
        <Button asChild className="w-full sm:w-fit">
          <Link to={`/jobs/${item.posting_id}`}>
            Check what the assistant filled and confirm
          </Link>
        </Button>
      ) : null}
      {item.state === "ready" && reviewable && !agentList ? (
        <div className="flex w-full flex-col gap-1 sm:w-fit">
          <Button
            className="sm:w-fit"
            disabled={
              busy ||
              !approval ||
              approvalFetching ||
              blocking.length > 0 ||
              unsaved > 0
            }
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
          ) : null}
        </div>
      ) : null}
      {canReject(item) ? (
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={onReject}
        >
          Reject
        </Button>
      ) : null}
      {canCancel(item) ? (
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={onCancel}
        >
          Cancel application
        </Button>
      ) : null}
      {canConfirmSubmitted(item) ? (
        <Button
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("submitted");
          }}
        >
          It was submitted
        </Button>
      ) : null}
      {canRetry(item) ? (
        <Button
          variant={canConfirmSubmitted(item) ? "outline" : "default"}
          className="w-full sm:w-fit"
          disabled={busy}
          onClick={() => {
            onAsk("retry");
          }}
        >
          Try again
        </Button>
      ) : null}
      {needsPostingPage(item) ? (
        <Button asChild variant="outline" className="w-full sm:w-fit">
          <Link to={`/jobs/${item.posting_id}`}>Open the posting</Link>
        </Button>
      ) : null}
    </>
  );
}
