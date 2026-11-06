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

/** The confirmation of a revert to the code's version, with the save's error if it failed. */
export function RevertDialog({
  name,
  open,
  error,
  busy,
  onCancel,
  onRevert,
}: {
  name: string;
  open: boolean;
  error: string | undefined;
  busy: boolean;
  onCancel: () => void;
  onRevert: () => void;
}) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Revert {name} to the code&apos;s version?
          </AlertDialogTitle>
          <AlertDialogDescription>
            A new version with no change is saved, so the history stays whole.
            The next run uses the code&apos;s definition, and unsaved changes on
            this page are dropped.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={busy}
            aria-busy={busy}
            onClick={onRevert}
          >
            {busy ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Revert
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
