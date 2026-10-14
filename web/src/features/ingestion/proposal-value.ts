/**
 * The value of a proposal as the parts a form can edit (task G13), and the reading of the
 * server's refusal of an edited value. No component here: `proposal-edit.tsx` renders it.
 */

/** The name of the only field of a value that is not a record. */
const WHOLE = "";
/** A string longer than this is edited in a text area. */
const LINE_MAX = 80;

type Kind = "line" | "text" | "number" | "lines" | "flag";
export interface Field {
  name: string;
  label: string;
  kind: Kind;
  held: unknown;
}
export type Draft = string | boolean;

export function words(name: string): string {
  const spaced = name.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function kindOf(name: string, held: unknown, long: boolean): Kind | null {
  if (held === null || typeof held === "string") {
    const text = typeof held === "string" ? held : "";
    return long ||
      name === "body" ||
      text.includes("\n") ||
      text.length > LINE_MAX
      ? "text"
      : "line";
  }
  if (typeof held === "number") return "number";
  if (typeof held === "boolean") return "flag";
  if (Array.isArray(held) && held.every((one) => typeof one === "string"))
    return "lines";
  return null;
}

export function fieldsOf(
  value: unknown,
  title: string,
  freeText: boolean,
): Field[] {
  if (!isRecord(value)) {
    const kind = kindOf(WHOLE, value, freeText);
    return kind ? [{ name: WHOLE, label: title, kind, held: value }] : [];
  }
  return Object.entries(value).flatMap(([name, held]) => {
    // The key of an entry is the server's: it is computed from the value again.
    const kind = name === "key" ? null : kindOf(name, held, false);
    return kind ? [{ name, label: words(name), kind, held }] : [];
  });
}

/** Whether the value has at least one part this form can edit. */
export function editable(value: unknown): boolean {
  return fieldsOf(value, "", false).length > 0;
}

export function draftOf(field: Field): Draft {
  if (field.kind === "flag") return field.held === true;
  if (field.kind === "lines") return (field.held as string[]).join("\n");
  return typeof field.held === "string" || typeof field.held === "number"
    ? String(field.held)
    : "";
}

export function valueOf(field: Field, draft: Draft): unknown {
  if (field.kind === "flag") return draft === true;
  const text = typeof draft === "string" ? draft : "";
  if (field.kind === "lines")
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "");
  if (field.kind === "number") {
    // Not a number: sent as typed, so the server says what it expects at this field.
    const number = Number(text);
    return text.trim() === "" ? null : Number.isFinite(number) ? number : text;
  }
  // A field that held nothing and is left empty still holds nothing.
  return field.held === null && text.trim() === "" ? null : text;
}

const REFUSED = /^edited_value(?:\.([^:\s]+))?: ([\s\S]+)$/;

/** The field a 422 sentence names (its first path part) and the message without that path. */
export function refusedField(
  detail: string,
): { name: string; message: string } | undefined {
  const match = REFUSED.exec(detail);
  if (!match) return undefined;
  const message = (match[2] ?? "").replace(/^Value error, /, "");
  return {
    name: (match[1] ?? WHOLE).split(".")[0] ?? WHOLE,
    message: /[.!?]$/.test(message) ? message : `${message}.`,
  };
}
