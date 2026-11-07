import { useId, type ReactNode } from "react";

import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from "@/components/ui/field";
import { RadioGroupItem } from "@/components/ui/radio-group";

import type { CredentialKind } from "@/features/agents/api/agents-api";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";

/** One kind of credential: the radio, its name and what it is, as the app's other radios. */
export function CredentialKindOption({
  kind,
  children,
}: {
  kind: CredentialKind;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <Field orientation="horizontal" className="gap-2">
      <RadioGroupItem id={id} value={kind} aria-describedby={`${id}-hint`} />
      <FieldContent className="gap-0.5">
        <FieldLabel htmlFor={id}>{KIND_LABEL[kind]}</FieldLabel>
        <FieldDescription id={`${id}-hint`}>{children}</FieldDescription>
      </FieldContent>
    </Field>
  );
}
