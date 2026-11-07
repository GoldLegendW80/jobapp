import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";

import type { Consent } from "@/features/ingestion/api/runs-api";

/** What is sent, and to whom, with the box that agrees to that version of the text. */
export function ConsentBox({
  consent,
  ticked,
  changed,
  disabled,
  onTick,
}: {
  consent: Consent;
  ticked: boolean;
  /** The text changed after the box was ticked: the tick no longer counts. */
  changed: boolean;
  disabled: boolean;
  /** The version of the text the box is ticked for, or null once unticked. */
  onTick: (version: number | null) => void;
}) {
  const boxId = useId();
  return (
    <section
      aria-labelledby={`${boxId}-heading`}
      className="flex flex-col gap-2 rounded-lg border p-3"
    >
      <h4 id={`${boxId}-heading`} className="text-sm font-semibold">
        What is sent, and to whom
      </h4>
      <p className="text-sm" data-testid="consent-text">
        {consent.text}
      </p>
      {changed ? (
        <p className="text-sm font-medium" data-testid="consent-changed">
          The text changed after you ticked the box. Read it again and tick the
          box if you agree.
        </p>
      ) : null}
      <Field orientation="horizontal" className="items-start">
        <Checkbox
          id={boxId}
          className="mt-0.5"
          checked={ticked}
          disabled={disabled}
          onCheckedChange={(state) => {
            onTick(state === true ? consent.version : null);
          }}
        />
        <FieldLabel htmlFor={boxId}>
          I have read this and I agree to send the selected documents
        </FieldLabel>
      </Field>
    </section>
  );
}
