import { useWatch, type UseFormReturn } from "react-hook-form";

import { Slider } from "@/components/ui/slider";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { WORDING } from "@/lib/wording";

import type { SkillsForm } from "@/features/profile/helpers/editor-forms";
import {
  ItemControls,
  LabelText,
  TextField,
} from "@/components/shared/profile/section-ui";
import { TermChips } from "@/components/shared/profile/term-chips";

/** One requirement the person does not meet (a gate): its name, terms and the points it takes off. */
export function RequirementFields({
  form,
  index,
  count,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<SkillsForm>;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const n = index + 1;
  const name = useWatch({ control: form.control, name: `gates.${index}.name` });
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-2">
        <TextField
          control={form.control}
          name={`gates.${index}.name`}
          label="Name"
          srSuffix={`of requirement ${n}`}
          autoComplete="off"
          className="min-w-0 flex-1"
        />
        <ItemControls
          index={index}
          count={count}
          name={`requirement ${n}`}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      <TermChips
        form={form}
        name={`gates.${index}`}
        label={name.trim() || `requirement ${n}`}
        title="Terms"
        compact
      />
      <FormField
        control={form.control}
        name={`gates.${index}.penalty`}
        render={({ field }) => (
          <FormItem>
            <div className="flex items-baseline justify-between gap-2">
              <FormLabel>
                <LabelText
                  label={WORDING.pointsTakenOff}
                  srSuffix={`of requirement ${n}`}
                />
              </FormLabel>
              <output className="text-sm tabular-nums text-muted-foreground">
                {field.value} points
              </output>
            </div>
            <FormControl>
              <Slider
                min={0}
                max={100}
                step={5}
                name={field.name}
                aria-label={`${WORDING.pointsTakenOff} of requirement ${String(n)}`}
                value={[field.value]}
                onBlur={field.onBlur}
                onValueChange={([value]) => {
                  if (value !== undefined) field.onChange(value);
                }}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  );
}
