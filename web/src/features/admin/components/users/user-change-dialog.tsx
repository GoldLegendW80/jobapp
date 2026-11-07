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

import { useUserChange } from "@/features/admin/api/use-user-change";
import {
  confirmationWords,
  type PendingChange,
} from "@/features/admin/helpers/user-actions";

/** The confirmation of an action on an account, open while one is pending. */
export function UserChangeDialog({
  pending,
  onClose,
}: {
  pending: PendingChange | null;
  onClose: () => void;
}) {
  const { change, changing, error, clearError } = useUserChange(onClose);
  const words = pending ? confirmationWords(pending) : null;
  const destructive =
    pending?.action === "disable" || pending?.action === "demote";

  return (
    <AlertDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !changing) {
          clearError();
          onClose();
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="break-words">
            {words?.title}
          </AlertDialogTitle>
          <AlertDialogDescription className="break-words">
            {words?.body}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={changing}>Cancel</AlertDialogCancel>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={changing}
            aria-busy={changing}
            onClick={() => {
              if (pending) change(pending);
            }}
          >
            {changing ? (
              <>
                <Spinner aria-hidden="true" role={undefined} />
                {words?.busy}
              </>
            ) : (
              words?.confirm
            )}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
