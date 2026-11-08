import { Plus } from "lucide-react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";

import {
  CATEGORIES_MAX,
  NEW_CATEGORY,
  SKILL_LISTS,
  type SkillList,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import { SKILL_LIST_LABELS } from "@/features/profile/helpers/skill-list-labels";
import { FoldedGroup } from "../about/folded-group";
import { CategoryFields } from "./category-fields";

/** The three lists of categories in words, items of the Accordion around them, each folded under the names it holds. */
export function CategoryLists({ form }: { form: UseFormReturn<SkillsForm> }) {
  return SKILL_LISTS.map((list) => (
    <CategoryList key={list} form={form} list={list} />
  ));
}

/** One of the three lists: its categories, and the button that adds one. */
function CategoryList({
  form,
  list,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
}) {
  const categories = useFieldArray({ control: form.control, name: list });
  const meta = SKILL_LIST_LABELS[list];
  const title = meta.inWords;
  // "What I have" reads "what I have" inside a sentence: only its first letter goes down.
  const inSentence = title.charAt(0).toLowerCase() + title.slice(1);
  const names = useWatch({ control: form.control, name: list })
    .map((category) => category.name.trim())
    .filter(Boolean);
  const add = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-label={`Add category to ${inSentence}`}
      disabled={categories.fields.length >= CATEGORIES_MAX}
      onClick={() => {
        categories.append(
          { ...NEW_CATEGORY, advanced_regex: false, terms: [] },
          { shouldFocus: true },
        );
      }}
    >
      <Plus aria-hidden="true" />
      Add category
    </Button>
  );
  return (
    <FoldedGroup
      value={list}
      title={title}
      summary={names.length > 0 ? names.join(", ") : "No category yet"}
      description={meta.description}
      action={add}
    >
      {categories.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No category yet.</p>
      ) : (
        <ol className="grid gap-x-8 gap-y-6 lg:grid-cols-2">
          {categories.fields.map((item, index) => (
            <li key={item.id} className="min-w-0">
              <CategoryFields
                form={form}
                list={list}
                index={index}
                count={categories.fields.length}
                where={`category ${index + 1} of ${inSentence}`}
                onMove={categories.move}
                onRemove={categories.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </FoldedGroup>
  );
}
