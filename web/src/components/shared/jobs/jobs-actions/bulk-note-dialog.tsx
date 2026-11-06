import { CircleAlert } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
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
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/api";

import { bulkUpdate } from "@/features/jobs/api/jobs-api";
import { plural } from "@/features/jobs/helpers/selection";

/** "Add note" of the bulk bar: one note appended to each selected posting's notes. */
export function BulkNoteDialog({
  ids,
  onClose,
  onDone,
}: {
  ids: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    setPending(true);
    setError("");
    bulkUpdate(ids, { notes_append: text.trim() }).then(
      (result) => {
        setPending(false);
        toast.success(`Note added to ${plural(result.changed, "posting")}.`);
        onDone();
      },
      (failure: unknown) => {
        setPending(false);
        setError(`Nothing was changed. ${describeError(failure)}`);
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Add a note to {plural(ids.length, "posting")}
          </DialogTitle>
          <DialogDescription>
            The note goes on a new line at the end of each posting&apos;s notes.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-2">
          <Field className="gap-2">
            <FieldLabel htmlFor={`${id}-note`}>Note</FieldLabel>
            <Textarea
              id={`${id}-note`}
              value={text}
              rows={4}
              maxLength={100_000}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
          </Field>
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
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
            disabled={pending || !text.trim()}
            aria-busy={pending}
          >
            {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
            Add note
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
