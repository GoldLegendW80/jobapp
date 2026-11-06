import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

import type { VersionSummary } from "@/features/documents/api/documents-api";

/** Asks before a version's text replaces the draft; open while `version` is set. */
export function RestoreVersionDialog({
  version,
  busy,
  onCancel,
  onRestore,
}: {
  version: VersionSummary | undefined;
  busy: boolean;
  onCancel: () => void;
  onRestore: (number: number) => void;
}) {
  return (
    <AlertDialog
      open={version !== undefined}
      onOpenChange={(value) => {
        if (!value && !busy) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Restore version {version?.number} into the draft?
          </AlertDialogTitle>
          <AlertDialogDescription>
            The draft takes its text and a new page is drawn from it. Version{" "}
            {version?.number} itself stays as it is
            {version?.frozen_at ? ", as it was sent" : ""}.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button variant="outline" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              if (version) onRestore(version.number);
            }}
          >
            Restore
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
