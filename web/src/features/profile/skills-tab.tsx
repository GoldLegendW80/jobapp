/**
 * Skills: the scoring model. Years of experience; three lists of categories (have, adjacent,
 * lacking), each a name and term chips with its own advanced switch; gates with a penalty; and a
 * "Try it" box that matches a pasted posting against the categories and gates being edited,
 * with the server's matching rules ported to the browser (matching.ts).
 */
import { Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import type { Profile } from "./api";
import {
  CATEGORIES_MAX,
  fromSkillsForm,
  GATES_MAX,
  NEW_CATEGORY,
  NEW_GATE,
  SKILL_LISTS,
  skillsSchema,
  toSkillsForm,
  type CategoryForm,
  type SkillList,
  type SkillsForm,
} from "./editor-forms";
import { compileTerms, findMatches } from "./matching";
import {
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  LabelText,
  SaveBar,
  TextField,
} from "./section-ui";
import { TermChips } from "./term-chips";
import { useSectionForm } from "./use-section-form";

const LISTS: Record<
  SkillList,
  { title: string; one: string; description: string }
> = {
  have: {
    title: "Competencies you have",
    one: "have",
    description:
      "A posting that asks for these raises the fit score. Each category is a name and the words that reveal it.",
  },
  adjacent: {
    title: "Adjacent competencies",
    one: "adjacent",
    description: "You partly cover these: they count for less.",
  },
  none: {
    title: "Competencies you lack",
    one: "lacking",
    description: "A posting that asks for these lowers the fit score.",
  },
};

export function SkillsTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "skills",
      profile,
      schema: skillsSchema,
      toForm: toSkillsForm,
      fromForm: fromSkillsForm,
      rules: { unique: { gates: "name" } },
      onDirtyChange,
    });
  const gates = useFieldArray({ control: form.control, name: "gates" });

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Skills"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Experience"
            description="Postings that ask for more years than this lose points."
          >
            <TextField
              control={form.control}
              name="years_experience"
              label="Years of experience"
              inputMode="decimal"
              autoComplete="off"
              placeholder="3.5"
              className="max-w-48"
            />
          </Group>
          {SKILL_LISTS.map((list) => (
            <CategoryList key={list} form={form} list={list} />
          ))}
          <Group
            title="Gates"
            description="Hard requirements you do not meet, such as a PhD or a clearance. A posting that names one loses its penalty."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={gates.fields.length >= GATES_MAX}
                onClick={() => {
                  gates.append(
                    { ...NEW_GATE, terms: [] },
                    { shouldFocus: true },
                  );
                }}
              >
                <Plus aria-hidden="true" />
                Add gate
              </Button>
            }
          >
            {gates.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No gate yet.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {gates.fields.map((item, index) => (
                  <li key={item.id} className="rounded-md border p-3 sm:p-4">
                    <GateFields
                      form={form}
                      index={index}
                      count={gates.fields.length}
                      onMove={gates.move}
                      onRemove={gates.remove}
                    />
                  </li>
                ))}
              </ol>
            )}
          </Group>
          <SaveBar
            label="Skills"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <TryIt form={form} />
      <ConflictDialog
        open={conflict}
        label="Skills"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}

function CategoryList({
  form,
  list,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
}) {
  const categories = useFieldArray({ control: form.control, name: list });
  const meta = LISTS[list];
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

function CategoryFields({
  form,
  list,
  index,
  count,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<SkillsForm>;
  list: SkillList;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const n = index + 1;
  const where = `${LISTS[list].one} category ${n}`;
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
      />
    </div>
  );
}

function GateFields({
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
          srSuffix={`of gate ${n}`}
          autoComplete="off"
          className="min-w-0 flex-1"
        />
        <ItemControls
          index={index}
          count={count}
          name={`gate ${n}`}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      <TermChips
        form={form}
        name={`gates.${index}`}
        label={name.trim() || `gate ${n}`}
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
                <LabelText label="Penalty" srSuffix={`of gate ${n}`} />
              </FormLabel>
              <output className="text-sm tabular-nums text-muted-foreground">
                {field.value} points
              </output>
            </div>
            <FormControl>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                className="w-full accent-primary"
                name={field.name}
                ref={field.ref}
                value={field.value}
                onBlur={field.onBlur}
                onChange={(event) => {
                  field.onChange(Number(event.target.value));
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

// --- Try it ------------------------------------------------------------------------------------

interface Result {
  name: string;
  /** what matched, lowercased, with how many times */
  hits: [string, number][];
  status: "match" | "miss" | "empty" | "unsupported" | "invalid";
}

function evaluate(category: CategoryForm, text: string): Result {
  const compiled = compileTerms(category.terms, category.advanced_regex);
  const name = category.name.trim() || "Unnamed";
  if (compiled.kind !== "ok") {
    return { name, hits: [], status: compiled.kind };
  }
  const counts = new Map<string, number>();
  for (const found of findMatches(compiled.regex, text)) {
    const key = found.toLocaleLowerCase("en");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return {
    name,
    hits: [...counts.entries()],
    status: counts.size > 0 ? "match" : "miss",
  };
}

function TryIt({ form }: { form: UseFormReturn<SkillsForm> }) {
  const [text, setText] = useState("");
  const values = useWatch({ control: form.control });
  const sections = useMemo(() => {
    if (text.trim() === "") return [];
    const lists: { title: string; categories: CategoryForm[] }[] = [
      ...SKILL_LISTS.map((list) => ({
        title: LISTS[list].title,
        categories: (values[list] ?? []) as CategoryForm[],
      })),
      { title: "Gates", categories: (values.gates ?? []) as CategoryForm[] },
    ];
    return lists.map((list) => ({
      title: list.title,
      results: list.categories.map((category) => evaluate(category, text)),
    }));
  }, [text, values]);

  return (
    <Group
      title="Try it"
      description="Paste a job posting to see which categories and gates match, with the rules search uses: whole words, any case, an optional plural. Uses the terms on this page, saved or not."
    >
      <div className="grid gap-2">
        <label htmlFor="skills-try-it" className="text-sm font-medium">
          Posting text
        </label>
        <Textarea
          id="skills-try-it"
          rows={6}
          value={text}
          placeholder="Paste the text of a posting here."
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </div>
      {sections.length > 0 ? (
        <div
          aria-label="Matches"
          role="region"
          aria-live="polite"
          className="grid gap-4 sm:grid-cols-2"
        >
          {sections.map((section) => {
            const matched = section.results.filter((r) => r.status === "match");
            return (
              <section key={section.title} className="min-w-0">
                <h4 className="text-sm font-medium">
                  {section.title}{" "}
                  <span className="font-normal text-muted-foreground">
                    ({matched.length} of {section.results.length} match)
                  </span>
                </h4>
                {section.results.length === 0 ? (
                  <p className="text-sm text-muted-foreground">None defined.</p>
                ) : (
                  <ul
                    aria-label={`${section.title} results`}
                    className="mt-1 flex flex-col gap-1 text-sm"
                  >
                    {section.results.map((result, index) => (
                      <li
                        key={`${result.name}-${index}`}
                        className="flex min-w-0 flex-wrap items-baseline gap-x-2"
                      >
                        <span
                          aria-hidden="true"
                          className={cn(
                            "inline-block size-2 shrink-0 rounded-full",
                            result.status === "match"
                              ? "bg-emerald-600 dark:bg-emerald-400"
                              : "border border-muted-foreground",
                          )}
                        />
                        <span
                          className={cn(
                            "font-medium",
                            result.status !== "match" &&
                              "text-muted-foreground",
                          )}
                        >
                          {result.name}
                        </span>
                        <span className="min-w-0 break-words text-muted-foreground">
                          {describe(result)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      ) : null}
    </Group>
  );
}

function describe(result: Result): string {
  switch (result.status) {
    case "match":
      return `matches ${result.hits
        .map(([word, count]) => (count > 1 ? `${word} (${count})` : word))
        .join(", ")}`;
    case "miss":
      return "no match";
    case "empty":
      return "no terms";
    case "unsupported":
      return "cannot be previewed here (Python only syntax); the server will still use it";
    case "invalid":
      return "a term is not a valid regular expression";
  }
}
