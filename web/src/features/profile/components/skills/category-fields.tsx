import { useWatch, type UseFormReturn } from "react-hook-form";

import type {
  SkillList,
  SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import {
  ItemControls,
  TextField,
} from "@/components/shared/profile/section-ui";
import { TermChips } from "@/components/shared/profile/term-chips";

/**
 * One category of a skills list: its name, its place in the list and its terms. `where` names it
 * for screen readers ("have category 2"); its terms are words.
 */
export function CategoryFields({
  form,
  list,
  index,
  count,
  where,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
  index: number;
  count: number;
  where: string;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const name = useWatch({
    control: form.control,
    name: `${list}.${index}.name`,
  });
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-2">
        <TextField
          control={form.control}
          name={`${list}.${index}.name`}
          label="Name"
          srSuffix={`of ${where}`}
          autoComplete="off"
          className="min-w-0 flex-1"
        />
        <ItemControls
          index={index}
          count={count}
          name={where}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      <TermChips
        form={form}
        name={`${list}.${index}`}
        label={name.trim() || where}
        title="Terms"
        compact
        regexSwitch={false}
      />
    </div>
  );
}
