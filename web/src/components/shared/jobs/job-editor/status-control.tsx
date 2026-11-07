import { useId, useState } from "react";
import { toast } from "sonner";

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
import { Field, FieldLabel } from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { failure, type Save } from "@/features/jobs/api/use-job-editor";
import { STATUSES, statusLabel } from "@/features/jobs/helpers/labels";

/** The posting's status, set at once; "applied" asks first, since it freezes the files. */
export function StatusControl({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const [confirming, setConfirming] = useState(false);

  const apply = (status: string) => {
    save({ status }, { status }).then(
      () => {
        toast.success(`Status set to ${statusLabel(status)}.`);
      },
      (error: unknown) => {
        toast.error(failure(error));
      },
    );
  };

  return (
    <Field className="gap-1.5">
      <FieldLabel htmlFor={id}>Status</FieldLabel>
      <NativeSelect
        id={id}
        value={job.status}
        onChange={(event) => {
          const next = event.target.value;
          if (next === job.status) return;
          if (next === "applied") setConfirming(true);
          else apply(next);
        }}
      >
        {!(STATUSES as readonly string[]).includes(job.status) ? (
          <option value={job.status}>{statusLabel(job.status)}</option>
        ) : null}
        {STATUSES.map((status) => (
          <option key={status} value={status}>
            {statusLabel(status)}
          </option>
        ))}
      </NativeSelect>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Mark this posting as applied?</AlertDialogTitle>
            <AlertDialogDescription>
              {job.applied_on
                ? "The applied date stays as it is."
                : "Today becomes its applied date."}{" "}
              The files of an applied posting are what the company received:
              they are never regenerated.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                setConfirming(false);
                apply("applied");
              }}
            >
              Mark as applied
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Field>
  );
}
