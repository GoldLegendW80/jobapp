/**
 * The four plain word lists of the search (owner, 2026-10-27): Job titles, Unwanted titles,
 * Places and Unwanted places, as chips of words. The person types the jobs and places she wants
 * and does not want, never a regular expression (owner, 2026-10-28): there is no switch. A list
 * saved as regular expressions by an earlier version keeps its terms and its mode, says so under
 * its chips, and still works (the search reads the words of each, app/profile/plain_terms.py).
 * Used by the profile's Search tab and by the Find jobs setup (jobs/search-setup.tsx).
 */
import type { UseFormReturn } from "react-hook-form";

import { WORDING } from "@/lib/wording";

import type { SearchForm } from "./editor-forms";
import { TermChips } from "./term-chips";

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

export function SearchLists({
  form,
  descriptions = true,
}: {
  form: UseFormReturn<SearchForm>;
  /** false in a dense dialog: the lists without their one line explanations */
  descriptions?: boolean;
}) {
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
    </div>
  );
}
