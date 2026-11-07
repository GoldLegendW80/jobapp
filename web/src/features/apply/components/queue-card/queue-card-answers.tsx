import { useId } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

import type { Answer, Approval } from "../../api/apply-api";
import { FieldsList } from "@/components/shared/apply/fields-list";

/**
 * The answers of a queue card, folded behind "Review the answers": the fields to read and correct
 * once the form is read, else why it could not be read with Try again, or that it is loading.
 */
export function QueueCardAnswers({
  applicationId,
  open,
  onOpenChange,
  approval,
  loadError,
  onRetry,
  editable,
  saving,
  onSave,
  onDirtyChange,
}: {
  applicationId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  approval: Approval | undefined;
  loadError: Error | null;
  onRetry: () => void;
  editable: boolean;
  saving: boolean;
  onSave: (answer: Answer, value: string | boolean) => Promise<boolean>;
  onDirtyChange: (count: number) => void;
}) {
  const panelId = useId();
  return (
    <Collapsible
      open={open}
      onOpenChange={onOpenChange}
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-medium">The answers on the form</h4>
        {/* The answers stay mounted while folded: an unsaved edit keeps Approve waiting. */}
        <CollapsibleTrigger asChild aria-controls={panelId}>
          <Button variant="outline" size="sm">
            {open ? "Hide the answers" : "Review the answers"}
          </Button>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent forceMount id={panelId} hidden={!open}>
        {approval ? (
          <FieldsList
            answers={approval.answers}
            editable={editable}
            saving={saving}
            onSave={onSave}
            onDirtyChange={onDirtyChange}
            applicationId={applicationId}
          />
        ) : loadError ? (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-col items-start gap-2">
              The answers could not be loaded. {describeError(loadError)}
              <Button variant="outline" size="sm" onClick={onRetry}>
                Try again
              </Button>
            </AlertDescription>
          </Alert>
        ) : (
          <p
            role="status"
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <Spinner aria-hidden="true" role={undefined} />
            Loading the answers
          </p>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
