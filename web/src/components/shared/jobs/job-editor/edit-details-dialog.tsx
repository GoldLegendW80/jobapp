import { CircleAlert } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { CURRENCIES } from "@/features/profile/api/profile-api";
import {
  PAY_PERIOD_WORDS,
  WORDING,
  WORK_MODE_WORDS,
  contractWord,
} from "@/lib/wording";

import { duplicateOf, type JobDetail } from "@/features/jobs/api/jobs-api";
import { failure, type Save } from "@/features/jobs/api/use-job-editor";
import {
  detailsOf,
  detailsPatch,
  type Details,
} from "@/features/jobs/helpers/job-details";
import { CONTRACT_TYPES } from "@/features/jobs/helpers/labels";

/** Company, title, location, contract type, work mode, pay and link of the posting; Save sends
 * only what changed, and a posting with the same details is linked. */
export function EditDetailsDialog({
  job,
  save,
  onClose,
}: {
  job: JobDetail;
  save: Save;
  onClose: () => void;
}) {
  const id = useId();
  const [values, setValues] = useState<Details>(() => detailsOf(job));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    text: string;
    duplicate?: string;
  } | null>(null);
  const missing = !values.company.trim() || !values.title.trim();

  const field = (name: keyof Details) => ({
    id: `${id}-${name}`,
    value: values[name],
    onChange: (event: { target: { value: string } }) => {
      setValues((old) => ({ ...old, [name]: event.target.value }));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (missing) return;
    const patch = detailsPatch(values, detailsOf(job));
    if (patch === "pay-invalid") {
      setError({ text: WORDING.payInvalid });
      return;
    }
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setPending(true);
    setError(null);
    save(patch).then(
      () => {
        setPending(false);
        toast.success("Details saved.");
        onClose();
      },
      (failed: unknown) => {
        setPending(false);
        const duplicate = duplicateOf(failed);
        if (duplicate)
          setError({
            text: "Another posting in your list has these details.",
            duplicate: duplicate.id,
          });
        else setError({ text: failure(failed) });
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit details</DialogTitle>
          <DialogDescription>
            Company, title, location, contract type, work mode, pay and link of
            this posting.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-3">
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-company`}>Company</FieldLabel>
            <Input {...field("company")} required maxLength={1000} />
          </Field>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-title`}>Title</FieldLabel>
            <Input {...field("title")} required maxLength={1000} />
          </Field>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-location`}>Location</FieldLabel>
            <Input {...field("location")} maxLength={1000} />
          </Field>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-contract_type`}>
              Contract type
            </FieldLabel>
            <NativeSelect {...field("contract_type")}>
              {CONTRACT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {contractWord(type)}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-work_mode`}>
              {WORDING.workMode}
            </FieldLabel>
            <NativeSelect {...field("work_mode")}>
              <option value="">{WORDING.notStated}</option>
              {Object.entries(WORK_MODE_WORDS).map(([mode, word]) => (
                <option key={mode} value={mode}>
                  {word}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <FieldSet className="gap-1.5">
            <FieldLegend variant="label" className="mb-1.5">
              {WORDING.pay}
            </FieldLegend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Input
                {...field("pay_min")}
                aria-label={WORDING.payLowest}
                inputMode="numeric"
                placeholder={WORDING.payFrom}
                maxLength={8}
              />
              <Input
                {...field("pay_max")}
                aria-label={WORDING.payHighest}
                inputMode="numeric"
                placeholder={WORDING.payTo}
                maxLength={8}
              />
              <NativeSelect
                {...field("pay_currency")}
                aria-label={WORDING.payCurrency}
              >
                {CURRENCIES.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </NativeSelect>
              <NativeSelect
                {...field("pay_period")}
                aria-label={WORDING.payPer}
              >
                {Object.entries(PAY_PERIOD_WORDS).map(([period, words]) => (
                  <option key={period} value={period}>
                    {words}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </FieldSet>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-url`}>Link to the posting</FieldLabel>
            <Input
              {...field("url")}
              type="url"
              inputMode="url"
              placeholder="https://"
              maxLength={1000}
            />
          </Field>
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{error.text}</p>
                {error.duplicate ? (
                  <Link
                    to={`/jobs/${error.duplicate}`}
                    className="underline underline-offset-4"
                    onClick={onClose}
                  >
                    Open the existing posting
                  </Link>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}
        </form>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            disabled={pending || missing}
            aria-busy={pending}
          >
            {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
