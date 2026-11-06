import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

interface ConfirmationProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** the section's label, as SECTION_LABELS writes it */
  label: string;
  revision: number;
  onRestore: () => void;
}

/** Asked before a restore when the tab holds unsaved changes. */
export function DiscardChangesDialog({
  open,
  onOpenChange,
  label,
  revision,
  onRestore,
}: ConfirmationProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            Your changes to {label} are not saved. Restoring revision {revision}{" "}
            replaces them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            Keep editing
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onOpenChange(false);
              onRestore();
            }}
          >
            Discard and restore
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** Asked after a restore met a save made elsewhere (a 409): restore over it, or not. */
export function ChangedElsewhereDialog({
  open,
  onOpenChange,
  label,
  revision,
  onRestore,
}: ConfirmationProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Your profile changed elsewhere</AlertDialogTitle>
          <AlertDialogDescription>
            It was saved from another tab or device after you opened this page.
            The comparison now shows the latest {label.toLowerCase()} as
            current. Restore revision {revision} over it anyway, or cancel.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              onOpenChange(false);
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={() => {
              onOpenChange(false);
              onRestore();
            }}
          >
            Restore anyway
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
