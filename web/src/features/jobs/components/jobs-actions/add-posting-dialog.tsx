import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
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
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/api";
import { contractWord } from "@/lib/wording";

import { addJob, duplicateOf, jobsKey } from "../../api/jobs-api";
import { CONTRACT_TYPES } from "@/features/jobs/helpers/labels";
import {
  EMPTY_POSTING,
  newPosting,
  type PostingDraft,
} from "@/features/jobs/helpers/new-posting";

/**
 * The "Add a posting" dialog. `initial` preselects fields (the Search tab's "Add a mission by
 * hand" preselects the contract Freelance, opportunities O8); `onAdded` replaces the default of
 * opening the new posting's page.
 */
export function AddPostingDialog({
  onClose,
  initial,
  onAdded,
}: {
  onClose: () => void;
  initial?: Partial<PostingDraft>;
  onAdded?: (job: { id: string }) => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<PostingDraft>({
    ...EMPTY_POSTING,
    ...initial,
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    text: string;
    existing?: string;
  } | null>(null);
  const missing = !draft.company.trim() || !draft.title.trim();

  const field = (name: keyof PostingDraft) => ({
    id: `${id}-${name}`,
    value: draft[name],
    onChange: (event: { target: { value: string } }) => {
      setDraft((old) => ({ ...old, [name]: event.target.value }));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (missing) return;
    setPending(true);
    setError(null);
    addJob(newPosting(draft)).then(
      (job) => {
        setPending(false);
        void client.invalidateQueries({ queryKey: jobsKey });
        toast.success(
          job.fit === null
            ? "Posting added."
            : `Posting added, fit ${job.fit}.`,
        );
        onClose();
        if (onAdded) onAdded(job);
        else void navigate(`/jobs/${job.id}`);
      },
      (failure: unknown) => {
        setPending(false);
        const duplicate = duplicateOf(failure);
        if (duplicate)
          setError({
            text: "This posting is already in your list.",
            existing: duplicate.id,
          });
        else setError({ text: `Not added. ${describeError(failure)}` });
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a posting</DialogTitle>
          <DialogDescription>
            For a posting from a site the app does not sweep. With its text, it
            is scored against your skills at once.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
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
                <option value="">Read from the title</option>
                {CONTRACT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {contractWord(type)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
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
          <Field className="gap-1.5">
            <FieldLabel htmlFor={`${id}-posting_text`}>Posting text</FieldLabel>
            <Textarea
              {...field("posting_text")}
              rows={8}
              maxLength={1_000_000}
              className="max-h-72 text-sm"
              placeholder="Paste the job description here."
            />
          </Field>
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{error.text}</p>
                {error.existing ? (
                  <Link
                    to={`/jobs/${error.existing}`}
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
          <Button variant="outline" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            disabled={pending || missing}
            aria-busy={pending}
          >
            {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Add posting
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
