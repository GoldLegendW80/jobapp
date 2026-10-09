/**
 * The api refuses an invalid section with 422 `{"detail": "data.<path>: <message>"}` (one
 * sentence, the first problem). `serverFieldError` turns that path into the name of the form
 * field to mark:
 * - a leaf path (`data.email`, `data.employers.2.start`) is that field;
 * - an item path (`data.employers.0`: end before start) is the item's `end`;
 * - a list path (`data.employers`: duplicate key) is the field of the first repeated item, by the
 *   list's identity field (key, name, country);
 * - a list of plain strings (`data.leave_blank.1`) is the item's `value` in the form.
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
  const message = sentence(match[2] ?? "");
  let node: unknown = values;
  for (const part of path) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
    if (node === undefined) return undefined;
  }
  const last = path[path.length - 1] ?? "";
  const name = path.join(".");
  if (Array.isArray(node)) {
    const field = rules.unique?.[last];
    if (!field) return undefined;
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
