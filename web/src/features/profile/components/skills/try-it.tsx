import { useMemo, useState } from "react";
import { useWatch, type UseFormReturn } from "react-hook-form";

import { Field, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import type { Skills } from "@/features/profile/api/profile-api";
import {
  inWords,
  toSkillsForm,
  type SkillsForm,
} from "@/features/profile/helpers/editor-forms";
import {
  describeMatch,
  matchPosting,
} from "@/features/profile/helpers/posting-matches";
import { Group } from "@/components/shared/profile/section-ui";

/**
 * A pasted posting matched against every category and requirement: the ones in words as stored
 * (`stored`, About you's) and the regular expressions being edited, saved or not.
 */
export function TryIt({
  form,
  stored,
}: {
  form: UseFormReturn<SkillsForm>;
  stored: Skills;
}) {
  const [text, setText] = useState("");
  const values = useWatch({ control: form.control }) as Partial<SkillsForm>;
  const words = useMemo(() => toSkillsForm(inWords(stored)), [stored]);
  const sections = useMemo(
    () =>
      matchPosting(
        {
          have: [...words.have, ...(values.have ?? [])],
          adjacent: [...words.adjacent, ...(values.adjacent ?? [])],
          none: [...words.none, ...(values.none ?? [])],
          gates: [...words.gates, ...(values.gates ?? [])],
        },
        text,
      ),
    [text, values, words],
  );

  return (
    <Group
      title="Try a posting"
      description="Paste a job posting to see which categories and requirements match, with the rules search uses: whole words, any case, an optional plural. Uses your skills in About you and the expressions above, saved or not."
    >
      <Field className="gap-2">
        <FieldLabel htmlFor="skills-try-it">Posting text</FieldLabel>
        <Textarea
          id="skills-try-it"
          rows={6}
          value={text}
          placeholder="Paste the text of a posting here."
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </Field>
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
                              ? "bg-success"
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
                          {describeMatch(result)}
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
