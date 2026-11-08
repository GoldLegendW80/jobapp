/** How many missing terms the line names before "and N more". */
export const NAMED_MAX = 8;

/** "A", "A and B", "A, B and C": the way the app writes a short list. */
function listed(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1) ?? ""}`;
}

/**
 * The line under a posting's CV that names the requirement terms it does not print, worded as a
 * fact to weigh: a term the person does not have stays out. Null when nothing is missing.
 */
export function missingSentence(missing: string[]): string | null {
  if (missing.length === 0) return null;
  const named = missing.slice(0, NAMED_MAX);
  const more = missing.length - named.length;
  const terms =
    more > 0 ? `${named.join(", ")} and ${String(more)} more` : listed(named);
  return missing.length === 1
    ? `${terms}, named by the posting, is not in this CV. Add it only if it is true of you.`
    : `${terms}, named by the posting, are not in this CV. Add one only if it is true of you.`;
}
