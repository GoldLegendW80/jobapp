/**
 * The words the documents screens use for the api's codes, in one place so the CV tab, the
 * document view and the posting cards say the same thing. No dashes as separators.
 */
import type {
  BaseSource,
  GenerationBrief,
  Origin,
  VersionSummary,
  WaitReason,
} from "./documents-api";

export const SOURCE_LABELS: Record<BaseSource, string> = {
  profile: "From your profile",
  built: "Built in the app",
  uploaded: "Uploaded",
};

export const ORIGIN_LABELS: Record<Origin, string> = {
  generated: "Written",
  edited: "Edited by hand",
  chat: "Changed in the chat",
  restored: "Restored",
  template: "Template or language change",
  uploaded: "Converted from your file",
  legacy: "Imported",
};

/** A bundled slug as a name ("classic" is "Classic"); the person's own are "From your CV". */
export function templateLabel(slug: string): string {
  if (slug.startsWith("upload-")) return "From your CV";
  const words = slug.split("-").filter((word) => word !== "");
  if (words.length === 0) return slug;
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const WAIT_SENTENCES: Record<WaitReason, string> = {
  consent:
    "Waiting for you to confirm what is sent. Open the CV tab of your profile.",
  credential: "Waiting for a model credential. Add one in your account.",
  base_cv: "Waiting for your base CV to exist.",
  daily_ceiling:
    "Waiting for tomorrow: the safety ceiling of the day is reached.",
};

/** The sentence for a code of the writer's fixed list of failures. */
export function failedSentence(code: string | null): string {
  switch (code) {
    case "token_limit":
      return "The writer reached its limit before it finished. Try again.";
    case "invalid_output":
    case "no_result":
      return "The writer did not return a usable CV. Try again.";
    default:
      return "The writer could not finish. Try again.";
  }
}

/** True while a generation will still produce something: waiting, queued or running. */
export function isOpen(
  generation: GenerationBrief | null | undefined,
): boolean {
  return (
    generation !== null &&
    generation !== undefined &&
    (generation.status === "waiting" ||
      generation.status === "queued" ||
      generation.status === "running")
  );
}

/** The state of a version in a few words: one page, overflow, or not rendered yet. */
export function fitLabel(version: VersionSummary): string {
  if (version.fit === "one_page") return "One page";
  if (version.fit === "overflow") return "Longer than one page";
  return "Not rendered yet";
}

const DATE = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE.format(date);
}

const NUMBER = new Intl.NumberFormat("en");

export const tokens = (value: number): string => NUMBER.format(value);

/** "about 12,000 to 20,000 tokens". */
export const tokenRange = (range: { low: number; high: number }): string =>
  `about ${tokens(range.low)} to ${tokens(range.high)} tokens`;

/** A decimal string as money: "USD 0.12". */
export function money(currency: string, value: string | number): string {
  const number = Number(value);
  return `${currency} ${Number.isFinite(number) ? number.toFixed(2) : String(value)}`;
}

/** The part of the record's path the person can read: "highlights.1" is "Highlights, item 2". */
export function fieldLabel(path: string): string {
  const parts = path.split(".").filter((part) => part !== "");
  if (parts.length === 0) return "Document";
  const [first, ...rest] = parts;
  const head = (first ?? "").replace(/_/g, " ");
  const words = head.charAt(0).toUpperCase() + head.slice(1);
  const items = rest.map((part) =>
    /^\d+$/.test(part) ? `item ${String(Number(part) + 1)}` : part,
  );
  return [words, ...items].join(", ");
}
