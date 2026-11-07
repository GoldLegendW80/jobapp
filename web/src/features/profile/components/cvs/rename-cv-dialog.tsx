import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

import { type BaseCv } from "@/features/documents/api/base-cvs-api";
import { failureSentence } from "@/features/documents/api/documents-api";
import { useUpdateBaseCv } from "@/features/documents/api/use-base-cvs";
import { CV_NAME_MAX } from "@/features/documents/helpers/base-cvs";

/** Renames a CV; the name starts as the current one each time the dialog opens (keyed). */
export function RenameCvDialog({
  cv,
  open,
  onClose,
}: {
  cv: BaseCv;
  open: boolean;
  onClose: () => void;
}) {
  const inputId = useId();
  const [name, setName] = useState(cv.name);
  const trimmed = name.trim();
  const save = useUpdateBaseCv(cv.id, { onUpdated: onClose });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
    >
      <DialogContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (trimmed !== "" && !save.isPending)
              save.mutate({ name: trimmed });
          }}
        >
          <DialogHeader>
            <DialogTitle>Rename this CV</DialogTitle>
            <DialogDescription>
              The name is only for you, in this list and in the selector of a
              posting.
            </DialogDescription>
          </DialogHeader>
          <Field className="gap-1.5">
            <FieldLabel htmlFor={inputId}>Name</FieldLabel>
            <Input
              id={inputId}
              value={name}
              maxLength={CV_NAME_MAX}
              autoComplete="off"
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
            {save.isError ? (
              <FieldError>{failureSentence(save.error)}</FieldError>
            ) : null}
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={save.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={trimmed === "" || save.isPending}>
              Save name
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
