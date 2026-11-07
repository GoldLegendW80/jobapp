/**
 * The api refuses an invalid section with 422 `{"detail": "data.<path>: <message>"}` (one
 * sentence, the first problem). `serverFieldError` turns that path into the name of the form
 * field to mark:
 * - a leaf path (`data.email`, `data.employers.2.start`) is that field;
 * - an item path (`data.employers.0`: end before start) is the item's `end`;
 * - a list path (`data.employers`: duplicate key) is the field of the first repeated item, by the
 *   list's identity field (key, name, country);
 * - a list of plain strings (`data.leave_blank.1`) is the item's `value` in the form;
 * - a refused term (`data.have.0.terms: term 2: invalid regular expression at position 3`) is
 *   that term of the list (`have.0.terms.1`), so the message shows under its chip. The api numbers
 *   terms after dropping duplicates, which the form never holds.
 * Anything else (a field the form does not show) gives undefined: the caller shows the sentence
 * above the form instead.
 */
import { errorDetail, isApiError } from "@/lib/api";

export interface FieldError {
  name: string;
  message: string;
}

export interface ListRules {
  /** list name: the field of an item that must be unique */
  unique?: Record<string, string>;
  /** lists of plain strings, held in the form as `{value}` objects */
  wrapped?: string[];
}

const DETAIL = /^data\.([^:\s]+): ([\s\S]+)$/;
const TERM = /^term (\d+): ([\s\S]+)$/;

const fold = (value: unknown) =>
  typeof value === "string" ? value.trim().toLocaleLowerCase("en") : "";

/** The 422 sentence of the api, or undefined for any other error. */
export function validationDetail(error: unknown): string | undefined {
  if (!isApiError(error) || error.status !== 422) return undefined;
  return errorDetail(error);
}

export function serverFieldError(
  detail: string,
  values: unknown,
  rules: ListRules = {},
): FieldError | undefined {
  const match = DETAIL.exec(detail);
  if (!match) return undefined;
  const path = (match[1] ?? "").split(".");
  const raw = match[2] ?? "";
  const message = sentence(raw);
  let node: unknown = values;
  for (const part of path) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
    if (node === undefined) return undefined;
  }
  const last = path[path.length - 1] ?? "";
  const name = path.join(".");
  if (Array.isArray(node)) {
    const term = TERM.exec(raw);
    if (term) {
      const index = Number(term[1]) - 1;
      if (index < 0 || index >= node.length) return undefined;
      return { name: `${name}.${index}`, message: sentence(term[2] ?? "") };
    }
    const field = rules.unique?.[last];
    // Only a duplicate names an item: a list wide rule (a size cap) stays above the form.
    if (!field || !/duplicate/i.test(raw)) return undefined;
    const seen = new Set<string>();
    for (const [index, item] of node.entries()) {
      const key = fold((item as Record<string, unknown>)[field]);
      if (seen.has(key)) return { name: `${name}.${index}.${field}`, message };
      seen.add(key);
    }
    return { name: `${name}.0.${field}`, message };
  }
  if (node !== null && typeof node === "object") {
    const parent = path[path.length - 2];
    if (parent && rules.wrapped?.includes(parent) && "value" in node) {
      return { name: `${name}.value`, message };
    }
    if (/\bend\b/i.test(message) && "end" in node) {
      return { name: `${name}.end`, message };
    }
    return undefined;
  }
  return { name, message };
}

/** "end must not be before start" becomes "End must not be before start." */
function sentence(message: string): string {
  const trimmed = message.trim();
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}
