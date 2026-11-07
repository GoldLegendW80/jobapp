import { CircleAlert } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

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
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { type RescoreResult } from "@/features/jobs/api/jobs-api";
import { useRescore } from "@/features/jobs/api/use-jobs-actions";
import { plural, rescoreSummary } from "@/features/jobs/helpers/selection";

/** Rescore the postings `ids`, or all of them, with the progress as it goes; fits set by hand
 * are kept unless the person ticks the box that replaces them. */
export function RescoreDialog({
  ids,
  onClose,
}: {
  ids?: string[];
  onClose: () => void;
}) {
  const id = useId();
  const [overwrite, setOverwrite] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<RescoreResult | null>(null);
  const [error, setError] = useState("");
  const what = ids
    ? `the ${plural(ids.length, "selected posting")}`
    : "all postings";

  const rescore = useRescore({
    onProgress: setProgress,
    onSuccess: (result) => {
      setRunning(false);
      toast.success(rescoreSummary(result));
      onClose();
    },
    onError: (failure) => {
      setRunning(false);
      setError(
        isApiError(failure) && failure.status === 409
          ? (errorDetail(failure) ?? "Rescoring is not possible now.")
          : `The rescore stopped. ${describeError(failure)}`,
      );
    },
  });

  const run = () => {
    setRunning(true);
    setError("");
    rescore.mutate({ ids, overwriteManual: overwrite });
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Rescore {what}?</AlertDialogTitle>
          <AlertDialogDescription>
            Each posting with a text is scored again against the skills of your
            profile. Fits you set by hand are kept unless you choose otherwise.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Field orientation="horizontal" className="items-start gap-2">
          <Checkbox
            id={`${id}-overwrite`}
            className="mt-0.5"
            checked={overwrite}
            disabled={running}
            onCheckedChange={(checked) => {
              setOverwrite(checked === true);
            }}
          />
          <FieldLabel htmlFor={`${id}-overwrite`} className="font-normal">
            Also replace the fits I set by hand
          </FieldLabel>
        </Field>
        {overwrite ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              Every fit you set by hand will be replaced by the computed one.
              This cannot be undone.
            </AlertDescription>
          </Alert>
        ) : null}
        {running ? (
          <p
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 text-sm"
          >
            <Spinner aria-hidden="true" role={undefined} />
            {progress
              ? `Rescoring: ${plural(progress.scored, "posting")} done${progress.remaining ? `, ${progress.remaining} to go` : ""}`
              : "Rescoring"}
          </p>
        ) : null}
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{error}</p>
              {error.includes("Profile") ? (
                <Link to="/profile" className="underline underline-offset-4">
                  Open your profile
                </Link>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={running}>Cancel</AlertDialogCancel>
          <Button
            variant={overwrite ? "destructive" : "default"}
            disabled={running}
            aria-busy={running}
            onClick={run}
          >
            {overwrite ? "Rescore and replace" : "Rescore"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
