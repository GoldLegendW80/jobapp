import { Trash2 } from "lucide-react";
import { useRef, useState, type RefObject } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail } from "@/lib/api";

import { useDeleteJobThread } from "@/features/agents/api/use-job-thread";

/** The bar above a conversation: Delete conversation, confirmed in a dialog. */
export function DeleteConversation({
  jobId,
  threadId,
  onDeleted,
  focusAfterDelete,
}: {
  jobId: string;
  threadId: string;
  onDeleted: () => void;
  /** The button that opened the dialog goes with the conversation: the focus goes here. */
  focusAfterDelete?: RefObject<HTMLElement | null>;
}) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const deleted = useRef(false);
  const remove = useDeleteJobThread(jobId, threadId, {
    onSuccess: () => {
      deleted.current = true;
      setOpen(false);
      onDeleted();
      toast.info("Conversation deleted.");
    },
    onError: (error) => {
      toast.error(errorDetail(error) ?? describeError(error));
    },
  });
  return (
    <div className="flex items-center justify-end border-b px-4 py-2">
      <Button
        ref={opener}
        variant="ghost"
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Trash2 aria-hidden="true" />
        Delete conversation
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          if (!remove.isPending) setOpen(next);
        }}
      >
        <AlertDialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            (deleted.current
              ? focusAfterDelete?.current
              : opener.current
            )?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              Its messages are deleted for good, and an answer being written is
              stopped. The agent&apos;s runs stay in the run log.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              disabled={remove.isPending}
              onClick={() => {
                setOpen(false);
              }}
            >
              Keep
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              aria-busy={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              {remove.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
