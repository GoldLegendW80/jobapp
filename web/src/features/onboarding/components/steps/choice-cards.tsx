import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

import { toggled } from "@/features/onboarding/helpers/steps";

/** One choice of a card: the whole box is the label, and it is marked while checked. */
const CHOICE =
  "min-h-11 w-full items-center rounded-md border px-3 font-normal leading-snug";

/** The card around a group of choices; its legend sits on the border. */
export const CHOICE_CARD = "gap-2 rounded-md border p-4";

/** A box alone on its row: the whole row is the label, a tap target of 44 px. */
export const ONE_BOX = "min-h-11 font-normal leading-snug";

/** A card of boxes to tick, any number of them. */
export function Choices<T extends string>({
  legend,
  options,
  words,
  chosen,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  words: (value: T) => string;
  chosen: readonly T[];
  onChange: (next: T[]) => void;
}) {
  const id = useId();
  return (
    <FieldSet className={CHOICE_CARD} data-simple-card>
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <FieldLabel
            key={option}
            htmlFor={`${id}-${option}`}
            className={CHOICE}
          >
            <Checkbox
              id={`${id}-${option}`}
              checked={chosen.includes(option)}
              onCheckedChange={(checked) => {
                onChange(toggled(chosen, option, checked === true));
              }}
            />
            {words(option)}
          </FieldLabel>
        ))}
      </div>
    </FieldSet>
  );
}

/** A card of radio buttons: one choice. */
export function Radios<T extends string>({
  legend,
  options,
  words,
  value,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  words: (value: T) => string;
  value: T | null;
  onChange: (next: T) => void;
}) {
  const id = useId();
  return (
    <FieldSet className={CHOICE_CARD} data-simple-card>
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <RadioGroup
        name={id}
        value={value ?? ""}
        className="grid-cols-1 gap-2 sm:grid-cols-2"
        onValueChange={(next) => {
          const option = options.find((o) => o === next);
          if (option !== undefined) onChange(option);
        }}
      >
        {options.map((option) => (
          <FieldLabel
            key={option}
            htmlFor={`${id}-${option}`}
            className={CHOICE}
          >
            <RadioGroupItem id={`${id}-${option}`} value={option} />
            {words(option)}
          </FieldLabel>
        ))}
      </RadioGroup>
    </FieldSet>
  );
}
