/** "Try a posting" of Advanced matching: which categories and requirements a posting matches. */
import { WORDING } from "@/lib/wording";

import {
  SKILL_LISTS,
  type CategoryForm,
  type SkillList,
  type SkillsForm,
} from "./editor-forms";
import { compileTerms, findMatches } from "./matching";
import { SKILL_LIST_LABELS } from "./skill-list-labels";

export interface CategoryMatch {
  name: string;
  /** what matched, lowercased, with how many times */
  hits: [string, number][];
  status: "match" | "miss" | "empty" | "unsupported" | "invalid";
}

export interface ListMatches {
  title: string;
  results: CategoryMatch[];
}

export function matchCategory(
  category: CategoryForm,
  text: string,
): CategoryMatch {
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

/** Every list of the form, then the requirements, matched against the text; none for no text. */
export function matchPosting(
  skills: Partial<Pick<SkillsForm, SkillList | "gates">>,
  text: string,
): ListMatches[] {
  if (text.trim() === "") return [];
  const lists: { title: string; categories: readonly CategoryForm[] }[] = [
    ...SKILL_LISTS.map((list) => ({
      title: SKILL_LIST_LABELS[list].title,
      categories: skills[list] ?? [],
    })),
    { title: WORDING.requirementsNotMet, categories: skills.gates ?? [] },
  ];
  return lists.map((list) => ({
    title: list.title,
    results: list.categories.map((category) => matchCategory(category, text)),
  }));
}

export function describeMatch(result: CategoryMatch): string {
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
