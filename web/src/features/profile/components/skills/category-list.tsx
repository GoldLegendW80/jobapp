import { Plus } from "lucide-react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  CATEGORIES_MAX,
  NEW_CATEGORY,
  type SkillList,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import { SKILL_LIST_LABELS } from "@/features/profile/helpers/skill-list-labels";
import { Group } from "@/components/shared/profile/section-ui";
import { CategoryFields } from "./category-fields";

/** One of the three lists of the Skills tab (have, adjacent, lacking): its categories. */
export function CategoryList({
  form,
  list,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
}) {
  const categories = useFieldArray({ control: form.control, name: list });
  const meta = SKILL_LIST_LABELS[list];
  return (
    <Group
      title={meta.title}
      description={meta.description}
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Add category to ${meta.title.toLowerCase()}`}
          disabled={categories.fields.length >= CATEGORIES_MAX}
          onClick={() => {
            categories.append(
              { ...NEW_CATEGORY, terms: [] },
              { shouldFocus: true },
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add category
        </Button>
      }
    >
      {categories.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No category yet.</p>
      ) : (
        <ol className="grid gap-3 lg:grid-cols-2">
          {categories.fields.map((item, index) => (
            <li key={item.id} className="min-w-0 rounded-md border p-3">
              <CategoryFields
                form={form}
                list={list}
                index={index}
                count={categories.fields.length}
                onMove={categories.move}
                onRemove={categories.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </Group>
  );
}
