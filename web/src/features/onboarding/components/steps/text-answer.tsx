import { useId, type ReactNode } from "react";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/** A field's label as tight as the plain Label it replaces, so the field keeps its height. */
export const TIGHT = "leading-none";

/** A one line answer with its label and a hint under it. */
export function TextAnswer({
  label,
  hint,
  value,
  onChange,
  readOnly = false,
  fieldId,
  maxLength = 2000,
  children,
}: {
  fieldId?: string;
  readOnly?: boolean;
  maxLength?: number;
  /** What the field offers under its hint: answers to pick in one click. */
  children?: ReactNode;
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const ownId = useId();
  const id = fieldId ?? ownId;
  return (
    <Field className="gap-1.5">
      <FieldLabel htmlFor={id} className={TIGHT}>
        {label}
      </FieldLabel>
      <Input
        id={id}
        value={value}
        readOnly={readOnly}
        maxLength={maxLength}
        aria-describedby={`${id}-hint`}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      <FieldDescription id={`${id}-hint`} className="text-xs">
        {hint}
      </FieldDescription>
      {children}
    </Field>
  );
}
