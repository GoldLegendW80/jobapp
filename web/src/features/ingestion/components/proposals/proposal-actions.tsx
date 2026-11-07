import { Check, Pencil, Undo2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import type { ProposalItem } from "../../api/proposals-api";
import { editable } from "@/features/ingestion/helpers/proposal-value";

/**
 * The buttons of a pending proposal: Accept (or "Accept the removal"), Edit and Reject. Accept is
 * left out when the profile cannot take the value as proposed, Edit when there is nothing to edit.
 */
export function ProposalActions({
  item,
  title,
  blocked,
  invalid,
  busy,
  accepting,
  canAccept,
  onAccept,
  onEdit,
  onReject,
}: {
  item: ProposalItem;
  title: string;
  /** The profile cannot take it as it is: an edit may fit, the value as proposed cannot. */
  blocked: boolean;
  invalid: boolean;
  busy: boolean;
  accepting: boolean;
  /** False while a conflict waits for a value to be picked. */
  canAccept: boolean;
  onAccept: () => void;
  onEdit: () => void;
  onReject: () => void;
}) {
  const removal = item.action === "remove";
  return (
    <div className="flex flex-wrap gap-2">
      {blocked ? null : (
        <Button
          size="sm"
          aria-label={`${removal ? "Accept the removal" : "Accept"}: ${title}`}
          disabled={busy || !canAccept}
          onClick={onAccept}
        >
          {accepting ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <Check aria-hidden="true" />
          )}
          {removal ? "Accept the removal" : "Accept"}
        </Button>
      )}
      {invalid || removal || !editable(item.value) ? null : (
        <Button
          variant="outline"
          size="sm"
          aria-label={`Edit: ${title}`}
          disabled={busy}
          onClick={onEdit}
        >
          <Pencil aria-hidden="true" />
          Edit
        </Button>
      )}
      <Button
        variant="outline"
        size="sm"
        aria-label={`Reject: ${title}`}
        disabled={busy}
        onClick={onReject}
      >
        <X aria-hidden="true" />
        Reject
      </Button>
    </div>
  );
}

/** "Take back" of an accepted proposal, as from the History drawer. */
export function TakeBackButton({
  title,
  busy,
  taking,
  onTakeBack,
}: {
  title: string;
  busy: boolean;
  taking: boolean;
  onTakeBack: () => void;
}) {
  return (
    <div>
      <Button
        variant="outline"
        size="sm"
        aria-label={`Take back: ${title}`}
        disabled={busy}
        onClick={onTakeBack}
      >
        {taking ? (
          <Spinner aria-hidden="true" role={undefined} />
        ) : (
          <Undo2 aria-hidden="true" />
        )}
        Take back
      </Button>
    </div>
  );
}
