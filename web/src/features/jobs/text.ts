/**
 * Helpers that keep untrusted job text safe and readable. Text is always rendered by React as
 * text (never as HTML), so these only decide structure and which links may be followed.
 */
import { statusLabel } from "./labels";

/** The URL as a link target only when it is http or https; anything else stays plain text. */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const value = raw.trim();
  // Browsers strip tabs, newlines and other control characters inside a URL.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(value)) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  return url.protocol === "http:" || url.protocol === "https:"
    ? url.href
    : null;
}

/** The host name of a URL, for a compact link label. */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Paragraphs of a posting: split on blank lines, single newlines kept inside a paragraph. */
export function paragraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n[ \t]*\n+/)
    .map((part) => part.replace(/^\n+|\n+$/g, ""))
    .filter((part) => part.trim() !== "");
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "none";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return JSON.stringify(value);
}

const FIELD_LABELS: Record<string, string> = {
  company: "company",
  title: "title",
  location: "location",
  contract_type: "contract type",
  url: "link",
  work_mode: "work mode",
  pay_min: "pay",
  pay_max: "pay",
  pay_currency: "pay",
  pay_period: "pay",
};

/** One line for an event of the timeline. */
export function describeEvent(
  kind: string,
  data: Record<string, unknown>,
): string {
  const via =
    data.via === "bulk"
      ? " (bulk action)"
      : data.via === "undo"
        ? " (undone)"
        : data.via === "rescore"
          ? " (rescore)"
          : data.via === "agent"
            ? " (agent proposal accepted)"
            : "";
  switch (kind) {
    case "created":
      return data.via === "import" ? "Imported" : "Added";
    case "status":
      return `Status changed from ${statusLabel(show(data.from))} to ${statusLabel(show(data.to))}${via}`;
    case "fit":
      if (data.manual === true && data.from === data.to)
        return `Fit ${show(data.to)} marked as set by hand`;
      if (data.manual === true)
        return `Fit set by hand from ${show(data.from)} to ${show(data.to)}`;
      if (data.was_manual === true)
        return `Fit back to the computed ${show(data.to)}, was ${show(data.from)} set by hand${via}`;
      return `Fit changed from ${show(data.from)} to ${show(data.to)}${via}`;
    case "note":
      if (data.via === "bulk") return "Note added (bulk action)";
      if (data.via === "agent") return "Note added (agent proposal accepted)";
      if (typeof data.to === "string" && data.to.trim() === "")
        return "Notes cleared";
      return "Notes edited";
    case "edit": {
      const changes = data.changes;
      const names =
        changes && typeof changes === "object"
          ? [
              ...new Set(
                Object.keys(changes).map((name) => FIELD_LABELS[name] ?? name),
              ),
            ]
          : [];
      return names.length ? `Edited the ${names.join(", ")}` : "Edited";
    }
    case "import":
      return "Updated by an import";
    case "sweep":
      return "Seen again by a sweep";
    default:
      return statusLabel(kind);
  }
}

/** Pretty JSON of a parsed value (two spaces), or the raw text if it is not JSON. */
export function prettyJson(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  // undefined for a value JSON cannot hold (a function, undefined itself).
  const text = JSON.stringify(value, null, 2) as string | undefined;
  return text ?? "";
}
