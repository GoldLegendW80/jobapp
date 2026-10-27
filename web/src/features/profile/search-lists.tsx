/**
 * The four plain word lists of the search (owner, 2026-10-27): Job titles, Unwanted titles,
 * Places and Unwanted places, as chips of words. Each list's regular expression switch sits
 * under the shared Advanced, off by default. Advanced opens by itself when a list holds regular
 * expressions (a saved one, or one a reload brings back), so the switch of a saved pattern shows
 * at load; the person can still fold it, and the list then says under its chips that it holds
 * expressions (search-lists.test.tsx).
 * Used by the profile's Search tab and by the Find jobs setup (jobs/search-setup.tsx).
 */
import { useState } from "react";
import { useWatch, type UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/advanced-section";
import { WORDING } from "@/lib/wording";

import type { SearchForm } from "./editor-forms";
import { TermChips, TermListRegexSwitch } from "./term-chips";

const LISTS = [
  {
    name: "title_include",
    label: WORDING.jobTitles,
    description: "A posting is kept when its title has one of these.",
  },
  {
    name: "title_exclude",
    label: WORDING.unwantedTitles,
    description: "A posting whose title has one of these is left out.",
  },
  {
    name: "location",
    label: WORDING.places,
    description:
      "Cities, countries or Remote. A posting is kept when its place has one of these. Leave it empty for anywhere.",
  },
  {
    name: "location_exclude",
    label: WORDING.unwantedPlaces,
    description:
      "A posting whose place has one of these is left out, even when it also names a place you want.",
  },
] as const;

const FLAGS = LISTS.map((list) => `${list.name}.advanced_regex` as const);

export function SearchLists({
  form,
  descriptions = true,
}: {
  form: UseFormReturn<SearchForm>;
  /** false in a dense dialog: the lists without their one line explanations */
  descriptions?: boolean;
}) {
  const flags = useWatch({ control: form.control, name: FLAGS });
  const usesRegex = flags.some(Boolean);
  const [open, setOpen] = useState(usesRegex);
  // A list that starts holding regular expressions (a reload, a reset) opens Advanced again.
  const [regexSeen, setRegexSeen] = useState(usesRegex);
  if (regexSeen !== usesRegex) {
    setRegexSeen(usesRegex);
    if (usesRegex) setOpen(true);
  }
  return (
    <div className="flex flex-col gap-4">
      {LISTS.map((list) => (
        <TermChips
          key={list.name}
          form={form}
          name={list.name}
          label={list.label}
          description={descriptions ? list.description : undefined}
          regexSwitch={false}
        />
      ))}
      <AdvancedSection open={open} onOpenChange={setOpen}>
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium">
            {WORDING.regularExpressions}
          </legend>
          <p className="text-xs text-muted-foreground">
            {WORDING.regularExpressionsNote}
          </p>
          {LISTS.map((list) => (
            <TermListRegexSwitch
              key={list.name}
              form={form}
              name={list.name}
              label={list.label}
            />
          ))}
        </fieldset>
      </AdvancedSection>
    </div>
  );
}
