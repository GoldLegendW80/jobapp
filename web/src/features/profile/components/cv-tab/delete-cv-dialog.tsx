import { useNavigate } from "react-router";
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

import { type BaseCv } from "@/features/documents/api/base-cvs-api";
import { failureSentence } from "@/features/documents/api/documents-api";
import { useDeleteBaseCv } from "@/features/documents/api/use-base-cvs";
import { CV_LIST_PATH } from "@/features/documents/helpers/base-cvs";

/** Asks before deleting a CV, and says what happens to the default and to the documents. */
export function DeleteCvDialog({
  cv,
  others,
  open,
  backToList,
  onClose,
}: {
  cv: BaseCv;
  /** How many CVs stay after this one. */
  others: number;
  open: boolean;
  backToList: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const remove = useDeleteBaseCv(cv.id, {
    onEnd: onClose,
    onDeleted: () => {
      if (backToList) void navigate(CV_LIST_PATH);
    },
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !remove.isPending) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {cv.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            {cv.is_default && others > 0
              ? "It is the default: the oldest other CV becomes the default. "
              : cv.is_default
                ? "It is your only CV: while documents are written automatically, a new one is built from your profile. "
                : ""}
            The documents already written for your postings from this CV stay as
            they are.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button
            variant="outline"
            disabled={remove.isPending}
            onClick={onClose}
          >
            Keep it
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => {
              remove.mutate();
            }}
          >
            Delete this CV
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
