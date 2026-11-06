import { useId } from "react";
import type { UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { SearchLists } from "@/components/shared/profile/search-lists";
import { TargetFields } from "@/components/shared/profile/target-fields";
import type { SearchForm } from "@/features/profile/helpers/editor-forms";
import { cn } from "@/lib/utils";

import {
  BLOCK,
  BLOCKS,
  COLUMN,
  HANG,
  StepNumber,
} from "@/components/shared/jobs/setup-grid";
import { CriteriaFields } from "./criteria-fields";

/** Step 2 of Find jobs: the titles and places wanted, the rest under More options (the words
 * not wanted, the criteria, what the person aims for, instructions for the assistant). */
export function WhatYouWant({
  form,
  more,
  onMoreChange,
  instructions,
  onInstructionsChange,
}: {
  form: UseFormReturn<SearchForm>;
  more: boolean;
  onMoreChange: (open: boolean) => void;
  instructions: string;
  onInstructionsChange: (text: string) => void;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={`${id}-want`}
      className={cn(COLUMN, BLOCKS, "border-t pt-6")}
    >
      <h3
        id={`${id}-want`}
        className={cn(
          HANG,
          "items-center text-base font-semibold tracking-tight",
        )}
      >
        <StepNumber step={2} />
        What you want
      </h3>
      {/* The ideas say what is missing without a link: leaving would drop the draft. */}
      <SearchLists
        form={form}
        only={["title_include", "location"]}
        links={false}
      />
      <AdvancedSection
        title="More options"
        keepMounted
        open={more}
        onOpenChange={onMoreChange}
      >
        <SearchLists form={form} only={["title_exclude", "location_exclude"]} />
        <CriteriaFields form={form} />
        {/* The profile's "What you aim for" box, without its border and padding here, so
          its words keep the setup's one edge. */}
        <div className="[&>section]:rounded-none [&>section]:border-0 [&>section]:p-0 sm:[&>section]:p-0">
          <TargetFields form={form} />
        </div>
        <Field className={BLOCK}>
          <FieldLabel htmlFor={`${id}-instructions`} className="leading-none">
            Instructions for the assistant
          </FieldLabel>
          <Textarea
            id={`${id}-instructions`}
            maxLength={2000}
            rows={3}
            value={instructions}
            placeholder="For example: only roles open to juniors, skip agencies."
            onChange={(event) => {
              onInstructionsChange(event.target.value);
            }}
          />
          <FieldDescription className="text-xs leading-4">
            Optional, up to 2,000 characters, kept for your next searches.
          </FieldDescription>
        </Field>
      </AdvancedSection>
    </section>
  );
}
