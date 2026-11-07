import type { ProfileField } from "../api/site-profile-api";

const REFUSALS = new Map([
  ["email", "Your e-mail address can only be changed by you on the site."],
  [
    "security",
    "Passwords, verification and security settings can only be changed by you.",
  ],
  [
    "privacy",
    "Privacy, visibility and recruiter contact settings can only be changed by you.",
  ],
  [
    "consent",
    "Notifications, marketing, communities, consent and terms are left for you.",
  ],
  [
    "deletion",
    "The assistant never deletes or withdraws anything from your site profile.",
  ],
  ["control", "This setting is left for you to change on the site."],
  [
    "no_source",
    "Your profile has no factual source for this field, so it is left empty.",
  ],
  [
    "autosave_unsupported",
    "This control cannot be written with an atomic empty-only check, so it is left for you.",
  ],
  [
    "factcheck",
    "This text includes a fact that your stored profile does not support.",
  ],
]);

const SOURCES = new Map([
  ["identity", "Identity"],
  ["experience", "Experience"],
  ["education", "Education"],
  ["skills", "Skills"],
  ["answers", "Answers"],
  ["document", "Reference CV"],
  ["person", "Your edit"],
  ["left", "Left for you"],
]);

/** The refusals only the person may lift: the field keeps its site value, with no input. */
const PERSON_ONLY = new Set([
  "email",
  "security",
  "privacy",
  "consent",
  "deletion",
  "control",
]);

/** Why the assistant left a field, in the person's words. */
export const refusalSentence = (refusal: string) =>
  REFUSALS.get(refusal) ?? "This field is left for you to review.";

/** Where a field's value comes from, in the person's words. */
export const fieldSourceLabel = (source: string) =>
  SOURCES.get(source) ?? source;

/** A value as the review table shows it: "Empty" for none. */
export const fieldValueText = (value: unknown) =>
  value == null || value === ""
    ? "Empty"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);

/** A value as its edit box holds it: empty for none. */
export const fieldEditText = (value: unknown) =>
  value == null || value === "" ? "" : fieldValueText(value);

export const canEditField = (field: ProfileField) =>
  field.kind !== "file" && !(field.refusal && PERSON_ONLY.has(field.refusal));

/** An ISO time to the microsecond, as [epoch milliseconds of the second, microseconds]. */
function preciseTime(value: string): readonly [number, number] | null {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(
      value,
    );
  if (!match) return null;
  const wall = Date.parse(`${match[1]}Z`);
  if (
    !Number.isFinite(wall) ||
    new Date(wall).toISOString().slice(0, 19) !== match[1]
  )
    return null;
  const seconds = Date.parse(`${match[1]}${match[3]}`);
  return Number.isFinite(seconds)
    ? [seconds, Number((match[2] ?? "").padEnd(6, "0"))]
    : null;
}

/** `current` is strictly later than `acknowledged`, to the microsecond; false if either is unreadable. */
export function newerTime(current: string, acknowledged: string): boolean {
  const left = preciseTime(current);
  const right = preciseTime(acknowledged);
  return (
    !!left &&
    !!right &&
    (left[0] > right[0] || (left[0] === right[0] && left[1] > right[1]))
  );
}
