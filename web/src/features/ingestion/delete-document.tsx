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
 * What deleting does (task G15). The promise is the owner's sentence (tasks/ingestion/todo.md,
 * decision 3), true once T10 keeps the server's backups 7 days, predeploy dumps included. The
 * provider sentence is the one of the consent text: deleting here cannot reach what was sent.
 */
export const DELETE_PROMISE =
  "Gone from the app now, and from the server's backups within 7 days.";
export const DELETE_PROVIDER =
  "It does not delete what Anthropic has already received.";
export const DELETE_REMOVES =
  "Items drawn from it that wait for your review are removed, and its quotes leave every proposal.";
export const DELETE_KEEPS =
  "What you already accepted stays in your profile, marked as from a deleted source.";

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
 * Asks before a document is deleted for good, and says exactly what "deleted" means: what goes,
 * what stays in the profile, the backups and the provider. A refusal by the server (a run or a
 * decision holds the document right now) is shown in its own words and the document stays.
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
                will be deleted for good, with its text, its page images and its
                transcriptions.
              </p>
              <p>{DELETE_REMOVES}</p>
              <p>{DELETE_KEEPS}</p>
              <p>{DELETE_PROMISE}</p>
              <p>{DELETE_PROVIDER}</p>
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
