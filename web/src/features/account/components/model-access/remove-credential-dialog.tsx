import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { message } from "@/features/admin/helpers/errors";

/** The confirmation before the saved credential is deleted. */
export function RemoveCredentialDialog({
  open,
  removing,
  error,
  onClose,
  onRemove,
}: {
  open: boolean;
  removing: boolean;
  error: Error | null;
  onClose: () => void;
  onRemove: () => void;
}) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !removing) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove the credential?</AlertDialogTitle>
          <AlertDialogDescription>
            Your agents stop running until you save a new one. The stored value
            is deleted from the server.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{message(error)}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={removing}
            aria-busy={removing}
            onClick={onRemove}
          >
            {removing ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Remove
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
