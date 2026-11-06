import { RotateCcw } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { describeError, errorDetail } from "@/lib/api";

import { isStale, type JobDetail } from "@/features/jobs/api/jobs-api";
import {
  failure,
  STALE_MESSAGE,
  type Save,
} from "@/features/jobs/api/use-job-editor";

/** The fit set by hand, 0 to 100, kept by every rescore; "Use computed" gives it back. */
export function FitControl({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const [value, setValue] = useState(job.fit === null ? "" : String(job.fit));
  const [shown, setShown] = useState(job.fit);
  if (job.fit !== shown) {
    setShown(job.fit);
    setValue(job.fit === null ? "" : String(job.fit));
  }
  const number = Number(value);
  const valid =
    value.trim() !== "" &&
    Number.isInteger(number) &&
    number >= 0 &&
    number <= 100;
  const unchanged = valid && number === job.fit && job.fit_overridden;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid || unchanged) return;
    save({ fit: number }, { fit: number, fit_overridden: true }).then(
      () => {
        toast.success(`Fit set by hand to ${number}.`);
      },
      (error: unknown) => {
        toast.error(failure(error));
      },
    );
  };

  const backToComputed = () => {
    save({ fit: null }, { fit_overridden: false }).then(
      (result) => {
        toast.success(
          result.fit === null
            ? "The fit is computed again."
            : `The fit is computed again: ${result.fit}.`,
        );
      },
      (error: unknown) => {
        toast.error(
          isStale(error)
            ? STALE_MESSAGE
            : `Not saved. ${errorDetail(error)?.replace(/^fit: /, "") ?? describeError(error)}`,
        );
      },
    );
  };

  return (
    <form onSubmit={submit}>
      <Field className="gap-1.5">
        <FieldLabel htmlFor={id}>Fit</FieldLabel>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={id}
            type="number"
            inputMode="numeric"
            min={0}
            max={100}
            step={1}
            value={value}
            aria-invalid={value !== "" && !valid}
            aria-describedby={`${id}-help`}
            onChange={(event) => {
              setValue(event.target.value);
            }}
            className="w-24 tabular-nums"
          />
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={!valid || unchanged}
          >
            Set by hand
          </Button>
          {job.fit_overridden ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={backToComputed}
            >
              <RotateCcw aria-hidden="true" />
              Use computed
            </Button>
          ) : null}
        </div>
        <FieldDescription id={`${id}-help`} className="text-xs leading-4">
          {job.fit_overridden
            ? "Set by hand: a rescore keeps it."
            : "Computed from the posting and your skills. A fit set by hand is kept by every rescore."}
        </FieldDescription>
      </Field>
    </form>
  );
}
