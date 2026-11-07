import { useEffect, useState } from "react";
import { useBlocker } from "react-router";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

/**
 * Asks before leaving a page with unsaved changes: another address of the app (a dialog with
 * Keep editing and Discard changes) or the tab itself (the browser's own question). `what` names
 * the forms that hold them. Used by the profile page and Settings, Search.
 */
export function UnsavedGuard({
  dirty,
  what,
  onDiscard,
}: {
  dirty: boolean;
  what: string;
  onDiscard: () => void;
}) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      currentLocation.pathname + currentLocation.search !==
        nextLocation.pathname + nextLocation.search,
  );
  // Set by Discard changes: the page may stay on screen while the next one loads, and the dialog
  // must not stay open over it.
  const [leaving, setLeaving] = useState(false);
  const blocked = blocker.state === "blocked";
  if (leaving && !blocked && blocker.state !== "proceeding") setLeaving(false);
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [dirty]);
  return (
    <AlertDialog
      open={blocked && !leaving}
      onOpenChange={(open) => {
        if (!open) blocker.reset?.();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            Your changes to {what} are not saved. Keep editing to save them
            first, or discard them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button variant="outline" onClick={() => blocker.reset?.()}>
            Keep editing
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              setLeaving(true);
              onDiscard();
              blocker.proceed?.();
            }}
          >
            Discard changes
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
