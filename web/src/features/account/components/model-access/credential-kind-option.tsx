import { useId, type ReactNode } from "react";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from "@/components/ui/field";
import { RadioGroupItem } from "@/components/ui/radio-group";
import { cn } from "@/lib/utils";

import type { CredentialKind } from "@/features/agents/api/agents-api";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";

/** One kind of credential: the radio, its name and what it is, in a box. */
export function CredentialKindOption({
  kind,
  checked,
  children,
}: {
  kind: CredentialKind;
  checked: boolean;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <Field
      orientation="horizontal"
      className={cn(
        "rounded-lg border p-3 transition-colors",
        checked ? "border-primary bg-accent/40" : "border-border",
      )}
    >
      <RadioGroupItem id={id} value={kind} aria-describedby={`${id}-hint`} />
      <FieldContent className="gap-1">
        <FieldLabel htmlFor={id} className="leading-none">
          {KIND_LABEL[kind]}
        </FieldLabel>
        <FieldDescription id={`${id}-hint`}>{children}</FieldDescription>
      </FieldContent>
    </Field>
  );
}
