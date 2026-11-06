import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { CURRENCIES } from "@/features/profile/api/profile-api";
import { WORDING } from "@/lib/wording";

import type { PayDraft } from "@/features/onboarding/helpers/steps";
import { TIGHT } from "./text-answer";

/** A pay range: from, to and its currency. `invalid` points the first box at the wizard's
 * problem. */
export function PayFields({
  id,
  invalid,
  legend,
  pay,
  onChange,
}: {
  id: string;
  invalid: boolean;
  legend: string;
  pay: PayDraft;
  onChange: (pay: PayDraft) => void;
}) {
  return (
    <FieldSet className="gap-2">
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Field className="gap-1.5">
          <FieldLabel htmlFor={`${id}-min`} className={TIGHT}>
            {WORDING.payFrom}
          </FieldLabel>
          <Input
            id={`${id}-min`}
            aria-invalid={invalid}
            aria-describedby={invalid ? "welcome-problem" : undefined}
            inputMode="numeric"
            value={pay.min}
            onChange={(event) => {
              onChange({ ...pay, min: event.target.value });
            }}
          />
        </Field>
        <Field className="gap-1.5">
          <FieldLabel htmlFor={`${id}-max`} className={TIGHT}>
            {WORDING.payTo}
          </FieldLabel>
          <Input
            id={`${id}-max`}
            inputMode="numeric"
            value={pay.max}
            onChange={(event) => {
              onChange({ ...pay, max: event.target.value });
            }}
          />
        </Field>
        <Field className="col-span-2 gap-1.5 sm:col-span-1">
          <FieldLabel htmlFor={`${id}-currency`} className={TIGHT}>
            {WORDING.payCurrency}
          </FieldLabel>
          <NativeSelect
            id={`${id}-currency`}
            value={pay.currency}
            onChange={(event) => {
              onChange({ ...pay, currency: event.target.value });
            }}
          >
            {CURRENCIES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
    </FieldSet>
  );
}
