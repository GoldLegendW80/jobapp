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
import {
  SKILL_LIST_LABELS,
  type SkillMode,
} from "@/features/profile/helpers/skill-list-labels";
import { Group } from "@/components/shared/profile/section-ui";
import { FoldedGroup } from "../about/folded-group";
import { SubGroup } from "../sub-group";
import { CategoryFields } from "./category-fields";

/** The categories written as regular expressions, in their three lists (have, adjacent, lacking). */
export function CategoriesGroup({ form }: { form: UseFormReturn<SkillsForm> }) {
  return (
    <Group
      title="Categories"
      description="A category is a name and the regular expressions that reveal it in a posting."
    >
      <CategoryLists form={form} mode="regex" />
    </Group>
  );
}

/**
 * The three lists of categories: as regular expressions, one under the other; in words, as items
 * of the Accordion around them, each folded under the names it holds.
 */
export function CategoryLists({
  form,
  mode,
}: {
  form: UseFormReturn<SkillsForm>;
  mode: SkillMode;
}) {
  const lists = SKILL_LISTS.map((list) => (
    <CategoryList key={list} form={form} list={list} mode={mode} />
  ));
  return mode === "words" ? (
    lists
  ) : (
    <div className="flex flex-col gap-8">{lists}</div>
  );
}

/** One of the three lists: its categories, and the button that adds one. */
function CategoryList({
  form,
  list,
  mode,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
  mode: SkillMode;
}) {
  const categories = useFieldArray({ control: form.control, name: list });
  const meta = SKILL_LIST_LABELS[list];
  const title = mode === "regex" ? meta.title : meta.inWords;
  // "What I have" reads "what I have" inside a sentence: only its first letter goes down.
  const inSentence = title.charAt(0).toLowerCase() + title.slice(1);
  const where = (n: number) =>
    mode === "regex"
      ? `${meta.one} category ${n}`
      : `category ${n} of ${inSentence}`;
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
          { ...NEW_CATEGORY, advanced_regex: mode === "regex", terms: [] },
          { shouldFocus: true },
        );
      }}
    >
      <Plus aria-hidden="true" />
      Add category
    </Button>
  );
  const body =
    categories.fields.length === 0 ? (
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
              where={where(index + 1)}
              regexSwitch={mode === "regex"}
              onMove={categories.move}
              onRemove={categories.remove}
            />
          </li>
        ))}
      </ol>
    );
  return mode === "words" ? (
    <FoldedGroup
      value={list}
      title={title}
      summary={names.length > 0 ? names.join(", ") : "No category yet"}
      description={meta.description}
      action={add}
    >
      {body}
    </FoldedGroup>
  ) : (
    <SubGroup title={title} description={meta.description} action={add}>
      {body}
    </SubGroup>
  );
}
