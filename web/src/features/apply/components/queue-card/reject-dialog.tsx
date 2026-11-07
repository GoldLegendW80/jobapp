import { useId, useState } from "react";

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
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

/** Reject asks first, opened fresh each time; "Skip this posting too" also sets the posting aside. */
export function RejectDialog({
  company,
  role,
  pending,
  onCancel,
  onConfirm,
}: {
  company: string;
  role: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: (skip: boolean) => void;
}) {
  const boxId = useId();
  const [skip, setSkip] = useState(false);
  return (
    <AlertDialog
      open
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Reject the application to {company} for {role}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            Nothing is sent, and it leaves the queue. The posting stays in your
            jobs list unless you skip it.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex items-start gap-2">
          <Checkbox
            id={boxId}
            className="mt-0.5"
            checked={skip}
            disabled={pending}
            onCheckedChange={(checked) => {
              setSkip(checked === true);
            }}
          />
          <Label htmlFor={boxId} className="leading-snug font-normal">
            Skip this posting too: it moves to Skipped in your jobs list.
          </Label>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Not yet</AlertDialogCancel>
          <Button
            onClick={() => {
              onConfirm(skip);
            }}
            disabled={pending}
          >
            {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Reject
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
