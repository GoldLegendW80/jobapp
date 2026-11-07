/** The base CV's own fixed facts (its `fixed` block): the three lists, their columns, and the key
 * an employer gets from its organisation. */
import { slugify } from "@/features/profile/helpers/forms";

import { MAX } from "./edit-content";
import { textOf } from "./edit-form";

export interface FixedColumn {
  name: string;
  label: string;
  /** A year or a month, typed as 2021 or 2021-03. */
  period?: boolean;
}

export interface FixedList {
  name: "employers" | "schools" | "languages";
  title: string;
  noun: string;
  max: number;
  columns: FixedColumn[];
}

export const FIXED_LISTS: FixedList[] = [
  {
    name: "employers",
    title: "Employers",
    noun: "Employer",
    max: MAX.fixedEmployers,
    columns: [
      { name: "organisation", label: "Organisation" },
      { name: "title", label: "Title" },
      { name: "start", label: "Start", period: true },
      { name: "end", label: "End, empty for today", period: true },
      { name: "location", label: "Location" },
    ],
  },
  {
    name: "schools",
    title: "Schools",
    noun: "School",
    max: MAX.fixedSchools,
    columns: [
      { name: "school", label: "School" },
      { name: "degree", label: "Degree" },
      { name: "start", label: "Start", period: true },
      { name: "end", label: "End", period: true },
      { name: "location", label: "Location" },
    ],
  },
  {
    name: "languages",
    title: "Languages",
    noun: "Language",
    max: MAX.fixedLanguages,
    columns: [
      { name: "name", label: "Language" },
      { name: "level", label: "Level" },
    ],
  },
];

/**
 * The key of the employer at `index` once its organisation reads `organisation`. The key is what
 * the CV's experience names (`used`): it is made from the organisation, unique in the list, until
 * the experience uses it, and then it stays.
 */
export function employerKey(
  rows: readonly unknown[],
  index: number,
  organisation: string,
  used: ReadonlySet<string>,
): string {
  const key = textOf((rows[index] as { key?: unknown } | undefined)?.key);
  if (key !== "" && used.has(key)) return key;
  const taken = new Set(
    rows
      .filter((_, i) => i !== index)
      .map((one) => textOf((one as { key?: unknown }).key)),
  );
  const stem = slugify(organisation) || "employer";
  let next = stem;
  for (let n = 2; taken.has(next); n += 1) next = `${stem}-${String(n)}`;
  return next;
}
