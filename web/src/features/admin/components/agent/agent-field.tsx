import { useId, type ReactNode } from "react";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";

/** An editable field of the agent: its problem, its hint, and the code's value once changed. */
export function AgentField({
  label,
  hideLabel = false,
  error,
  hint,
  code,
  changed,
  children,
}: {
  label: string;
  /** The section's heading beside the field already names it: the label is for screen readers. */
  hideLabel?: boolean;
  error?: string;
  hint?: string;
  code?: string;
  changed: boolean;
  children: (id: string, described: string) => ReactNode;
}) {
  const id = useId();
  return (
    <Field className="min-w-0 gap-2">
      <FieldLabel htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
        {label}
      </FieldLabel>
      {children(id, `${id}-help`)}
      <div id={`${id}-help`} className="flex flex-col gap-1 text-sm">
        {error ? <p className="text-destructive">{error}</p> : null}
        {hint ? (
          <FieldDescription className="nth-last-2:mt-0">
            {hint}
          </FieldDescription>
        ) : null}
        {changed && code !== undefined ? (
          <FieldDescription className="[overflow-wrap:anywhere]">
            The code&apos;s: {code}
          </FieldDescription>
        ) : null}
      </div>
    </Field>
  );
}
