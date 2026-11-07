import { Plus } from "lucide-react";
import {
  useFieldArray,
  useWatch,
  type Control,
  type FieldPath,
} from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group";

import {
  SALARY_RANGES_MAX,
  type AnswersForm,
} from "@/features/profile/helpers/forms";
import {
  ITEM,
  ITEM_LIST,
  ItemControls,
  LabelText,
  TextField,
} from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../folded-group";
import { ChoiceToggles } from "./choice-toggles";
import { labelOf, PERIODS, STANCES } from "./choices";

/** What forms get when they ask for a salary: the stance, and the ranges per currency. */
export function Salary({ control }: { control: Control<AnswersForm> }) {
  const ranges = useFieldArray({ control, name: "salary_ranges" });
  const stance = useWatch({ control, name: "salary_stance" });
  const count = ranges.fields.length;
  return (
    <FoldedGroup
      value="salary"
      title="Salary"
      summary={`${labelOf(STANCES, stance)}, ${
        count === 0
          ? "no range"
          : `${count} ${count === 1 ? "range" : "ranges"}`
      }`}
      description="What forms get when they ask for a salary. Never a figure you did not write."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={ranges.fields.length >= SALARY_RANGES_MAX}
          onClick={() => {
            ranges.append(
              { currency: "", minimum: "", maximum: "", period: "year" },
              { shouldFocus: true },
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add range
        </Button>
      }
    >
      <ChoiceToggles
        control={control}
        name="salary_stance"
        label="Salary stance"
        description="Automatic uses the salary policy sentence when it is filled and leaves the field blank otherwise."
        options={STANCES}
      />
      {ranges.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No range yet.</p>
      ) : (
        <ol className={ITEM_LIST}>
          {ranges.fields.map((item, index) => (
            <li key={item.id} className={ITEM}>
              <RangeFields
                control={control}
                index={index}
                count={ranges.fields.length}
                onMove={ranges.move}
                onRemove={ranges.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </FoldedGroup>
  );
}

function RangeFields({
  control,
  index,
  count,
  onMove,
  onRemove,
}: {
  control: Control<AnswersForm>;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const n = index + 1;
  const currency = useWatch({
    control,
    name: `salary_ranges.${index}.currency`,
  });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <h5 className="text-sm font-medium">Range {n}</h5>
        <ItemControls
          index={index}
          count={count}
          name={`range ${n}`}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      <div className="grid items-start gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <TextField
          control={control}
          name={`salary_ranges.${index}.currency`}
          label="Currency"
          srSuffix={`of range ${n}`}
          placeholder="EUR"
          maxLength={3}
          autoComplete="off"
        />
        <MoneyField
          control={control}
          name={`salary_ranges.${index}.minimum`}
          label="Minimum"
          srSuffix={`of range ${n}`}
          currency={currency}
        />
        <MoneyField
          control={control}
          name={`salary_ranges.${index}.maximum`}
          label="Maximum"
          srSuffix={`of range ${n}`}
          currency={currency}
        />
        <ChoiceToggles
          control={control}
          name={`salary_ranges.${index}.period`}
          label="Per"
          srSuffix={`of range ${n}`}
          options={PERIODS}
        />
      </div>
    </div>
  );
}

/** A whole amount, with the currency of its range beside it once one is typed. */
function MoneyField({
  control,
  name,
  label,
  srSuffix,
  currency,
}: {
  control: Control<AnswersForm>;
  name: FieldPath<AnswersForm>;
  label: string;
  srSuffix: string;
  currency: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>
            <LabelText label={label} srSuffix={srSuffix} />
          </FormLabel>
          <InputGroup>
            <FormControl>
              <InputGroupInput
                inputMode="numeric"
                autoComplete="off"
                name={field.name}
                ref={field.ref}
                value={typeof field.value === "string" ? field.value : ""}
                onBlur={field.onBlur}
                onChange={field.onChange}
              />
            </FormControl>
            {currency.trim() ? (
              <InputGroupAddon align="inline-end">
                <InputGroupText>{currency.trim().toUpperCase()}</InputGroupText>
              </InputGroupAddon>
            ) : null}
          </InputGroup>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
