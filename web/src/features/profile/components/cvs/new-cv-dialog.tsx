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
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";

import type {
  BaseCv,
  BaseCvRequest,
} from "@/features/documents/api/base-cvs-api";
import { CV_NAME_MAX } from "@/features/documents/helpers/base-cvs";

/** "New CV": from the profile or a copy of another CV, with an optional name. */
export function NewCvDialog({
  open,
  cvs,
  busy,
  onClose,
  onCreate,
}: {
  open: boolean;
  cvs: BaseCv[];
  busy: boolean;
  onClose: () => void;
  onCreate: (request: BaseCvRequest) => void;
}) {
  const nameId = useId();
  const copyId = useId();
  const sourceId = useId();
  const [source, setSource] = useState<"profile" | "copy">("profile");
  const [copyOf, setCopyOf] = useState("");
  const [name, setName] = useState("");
  const chosen = cvs.find((one) => one.id === copyOf)?.id ?? cvs[0]?.id;
  const trimmed = name.trim();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy) return;
            if (source === "copy" && chosen === undefined) return;
            onCreate({
              from: source,
              ...(source === "copy" ? { copy_of: chosen } : {}),
              ...(trimmed === "" ? {} : { name: trimmed }),
            });
          }}
        >
          <DialogHeader>
            <DialogTitle>New CV</DialogTitle>
            <DialogDescription>
              Start from your profile, or from a copy of a CV you already have.
            </DialogDescription>
          </DialogHeader>
          <FieldSet className="gap-2">
            <FieldLegend variant="label" className="mb-1">
              Start from
            </FieldLegend>
            <RadioGroup
              name="new-cv-source"
              value={source}
              onValueChange={(value) => {
                setSource(value === "copy" ? "copy" : "profile");
              }}
              className="gap-2"
            >
              <Field orientation="horizontal" className="gap-2">
                <RadioGroupItem value="profile" id={`${sourceId}-profile`} />
                <FieldLabel
                  htmlFor={`${sourceId}-profile`}
                  className="font-normal"
                >
                  My profile
                </FieldLabel>
              </Field>
              <Field orientation="horizontal" className="gap-2">
                <RadioGroupItem value="copy" id={`${sourceId}-copy`} />
                <FieldLabel
                  htmlFor={`${sourceId}-copy`}
                  className="font-normal"
                >
                  A copy of another CV
                </FieldLabel>
              </Field>
            </RadioGroup>
          </FieldSet>
          {source === "copy" ? (
            <Field className="gap-1.5">
              <FieldLabel htmlFor={copyId}>CV to copy</FieldLabel>
              <NativeSelect
                id={copyId}
                value={chosen ?? ""}
                onChange={(event) => {
                  setCopyOf(event.target.value);
                }}
              >
                {cvs.map((one) => (
                  <option key={one.id} value={one.id}>
                    {one.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          ) : null}
          <Field className="gap-1.5">
            <FieldLabel htmlFor={nameId}>Name (optional)</FieldLabel>
            <Input
              id={nameId}
              value={name}
              maxLength={CV_NAME_MAX}
              autoComplete="off"
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? <Spinner aria-hidden="true" role={undefined} /> : null}
              {source === "copy" ? "Make a copy" : "Build it"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
