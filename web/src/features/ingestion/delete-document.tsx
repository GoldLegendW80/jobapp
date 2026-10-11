import { useMutation } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useRef, useState, type RefObject } from "react";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

import {
  deleteDocument,
  failureSentence,
  type IngestionDocument,
} from "./documents-api";

/**
 * What deleting does, said without a number of days because none is true yet: the server
 * still keeps 14 nightly dumps, and its predeploy dumps by count, whatever their age.
 *
 * G15: the owner's sentence (tasks/ingestion/todo.md, decision 3) is "Gone from the app now,
 * and from the server's backups within 7 days." Put it here only once T10 (backups kept 7
 * days) is deployed and verified on the server, the predeploy dumps included.
 */
export const DELETE_PROMISE =
  "Gone from the app now. The server's backups still hold a copy for a while, until newer backups replace them.";

interface Props {
  /** The document to delete; the dialog is open while it is set. */
  document: IngestionDocument | undefined;
  onClose: () => void;
  onDeleted: (document: IngestionDocument) => void;
  /**
   * Where the keyboard focus goes once a document is deleted: the button that opened the
   * dialog is gone with its row, so the focus would otherwise be lost.
   */
  focusAfterDelete?: RefObject<HTMLElement | null>;
}

/**
 * Asks before a document is deleted for good, and says exactly what "deleted" means. A refusal
 * by the server (later: a document a run has read) is shown in its own words and the document
 * stays. Task G15 extends what this dialog says.
 */
export function DeleteDocument({
  document,
  onClose,
  onDeleted,
  focusAfterDelete,
}: Props) {
  // The last document asked about stays while the dialog closes, so its text does not vanish
  // before the dialog does.
  const [shown, setShown] = useState(document);
  if (document && document !== shown) setShown(document);
  if (!shown) return null;
  return (
    <Ask
      key={shown.id}
      document={shown}
      open={document !== undefined}
      onClose={onClose}
      onDeleted={onDeleted}
      focusAfterDelete={focusAfterDelete}
    />
  );
}

function Ask({
  document,
  open,
  onClose,
  onDeleted,
  focusAfterDelete,
}: Omit<Props, "document"> & { document: IngestionDocument; open: boolean }) {
  const deleted = useRef(false);
  const mutation = useMutation({
    mutationFn: () => deleteDocument(document.id),
    onSuccess: () => {
      deleted.current = true;
      onDeleted(document);
    },
  });
  const close = () => {
    if (mutation.isPending) return;
    mutation.reset();
    onClose();
  };
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <AlertDialogContent
        onCloseAutoFocus={(event) => {
          const target = focusAfterDelete?.current;
          if (!deleted.current || !target) return;
          event.preventDefault();
          target.focus();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this document?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-2 text-sm text-muted-foreground">
              <p className="break-words">
                <bdi className="font-medium text-foreground">
                  {document.name}
                </bdi>{" "}
                will be deleted for good, with its text.
              </p>
              <p>{DELETE_PROMISE}</p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {mutation.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {failureSentence(mutation.error)}
          </p>
        ) : null}
        <AlertDialogFooter>
          <Button
            variant="outline"
            disabled={mutation.isPending}
            onClick={close}
          >
            Keep it
          </Button>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            aria-busy={mutation.isPending}
            onClick={() => {
              mutation.mutate();
            }}
          >
            {mutation.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Delete for good
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
