/**
 * The four plain word lists of the search (owner, 2026-10-27): Job titles, Unwanted titles,
 * Places and Unwanted places, as chips of words. The person types the jobs and places she wants
 * and does not want, never a regular expression (owner, 2026-10-28): there is no switch. A list
 * saved as regular expressions by an earlier version keeps its terms and its mode, says so under
 * its chips, and still works (the search reads the words of each, app/profile/plain_terms.py).
 * Used by the profile's Search tab and by the Find jobs setup (jobs/search-setup.tsx).
 *
 * Under Job titles, the job titles agent's ideas, and under Places the place of the profile
 * (title-suggestions.tsx): a click adds one to the list being edited, the save stays the
 * person's. A list of regular expressions gets no idea (a plain word would change its meaning).
 */
import { useWatch, type UseFormReturn } from "react-hook-form";

import { WORDING } from "@/lib/wording";

import { TERMS_MAX, type SearchForm } from "./editor-forms";
import { TermChips } from "./term-chips";
import { PlaceSuggestion, TitleSuggestions } from "./title-suggestions";

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

export type SearchListName = (typeof LISTS)[number]["name"];

export function SearchLists({
  form,
  descriptions = true,
  only,
  links = true,
}: {
  form: UseFormReturn<SearchForm>;
  /** false in a dense dialog: the lists without their one line explanations */
  descriptions?: boolean;
  /** the lists to show, in their usual order; all four when absent */
  only?: readonly SearchListName[];
  /** false in a dialog: the ideas name what is missing without a link (title-suggestions.tsx) */
  links?: boolean;
}) {
  return (
    <div className="flex flex-col gap-4">
      {LISTS.filter((list) => !only || only.includes(list.name)).map((list) => (
        <div key={list.name} className="flex min-w-0 flex-col gap-3">
          <TermChips
            form={form}
            name={list.name}
            label={list.label}
            description={descriptions ? list.description : undefined}
            regexSwitch={false}
          />
          {list.name === "title_include" || list.name === "location" ? (
            <Ideas
              form={form}
              name={list.name}
              label={list.label}
              links={links}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** The ideas of one list, added to the form's draft of that list. */
function Ideas({
  form,
  name,
  label,
  links,
}: {
  form: UseFormReturn<SearchForm>;
  name: "title_include" | "location";
  label: string;
  links: boolean;
}) {
  const list = useWatch({ control: form.control, name });
  if (list.advanced_regex) return null;
  const add = (term: string) => {
    const terms = form.getValues(`${name}.terms`);
    if (terms.length >= TERMS_MAX) return;
    form.setValue(`${name}.terms`, [...terms, term], {
      shouldDirty: true,
      shouldValidate: form.formState.isSubmitted,
    });
  };
  return name === "title_include" ? (
    <TitleSuggestions
      current={list.terms}
      onAdd={add}
      label={label}
      links={links}
    />
  ) : (
    <PlaceSuggestion current={list.terms} onAdd={add} label={label} />
  );
}
