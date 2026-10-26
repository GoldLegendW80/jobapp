/**
 * Calls of the Applications settings page (SPEC-apply.md, "API": /api/apply/settings,
 * /api/apply/caps, /api/apply/answer-bank; the routes of api/app/apply/settings_router.py).
 * Bodies are checked with zod. Every route reads and writes the signed in person only.
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

const TIMEOUT_MS = 15_000;

/** The bounds of a cap, as the api checks them (api/app/apply/caps_models.py). */
export const LABEL_MAX = 200;
export const COUNT_MAX = 100;
export const DAYS_MAX = 3650;
export const CAPS_MAX = 100;
/** The longest saved answer (api/app/apply/bank.py, VALUE_MAX). */
export const ANSWER_MAX = 5000;
export const QUESTION_MAX = 200;

const settingsSchema = z.object({
  mode_after_like: z.string(),
  full_auto_available: z.boolean(),
  paused: z.boolean(),
  min_fit: z.number().int(),
  min_fit_chosen: z.boolean(),
  safety_ceiling: z.number().int(),
  hold_s: z.number().int(),
  hold_min_s: z.number().int(),
  company_days: z.number().int(),
  show_postings_from: z.number().int().nullable(),
  write_documents_from: z.number().int().nullable(),
  rehearsals: z.number().int(),
  rehearsals_done: z.number().int(),
  full_auto_since: z.string().nullable(),
});
export type ApplySettings = z.infer<typeof settingsSchema>;

const capSchema = z.object({
  id: z.string(),
  label: z.string(),
  max_count: z.number().int(),
  window_days: z.number().int(),
  same_role_days: z.number().int().nullable(),
  used: z.number().int(),
});
export type Cap = z.infer<typeof capSchema>;

const capsSchema = z.object({
  items: z.array(capSchema),
  revision: z.string(),
});
export type Caps = z.infer<typeof capsSchema>;

/** One cap as the PUT takes it: no id, since the api replaces the whole list. */
export type CapDraft = Pick<
  Cap,
  "label" | "max_count" | "window_days" | "same_role_days"
>;

const bankEntrySchema = z.object({
  question_key: z.string(),
  question: z.string(),
  answer: z.unknown(),
  used_count: z.number().int(),
  updated_at: z.string(),
});
export type BankEntry = z.infer<typeof bankEntrySchema>;

const bankSchema = z.object({ items: z.array(bankEntrySchema) });
export type Bank = z.infer<typeof bankSchema>;

const refusalSchema = z.object({ detail: z.string(), code: z.string() });
export type SettingsRefusal = z.infer<typeof refusalSchema>;

export const applySettingsKey = ["apply", "settings"] as const;
export const capsKey = ["apply", "caps"] as const;
export const answerBankKey = ["apply", "answer-bank"] as const;

export const getApplySettings = (
  signal?: AbortSignal,
): Promise<ApplySettings> =>
  request("/apply/settings", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => settingsSchema.parse(body),
  });

/** The mode, the pause switch, "Apply automatically from" and the hold; a field left out is not changed. */
export const saveApplySettings = (change: {
  mode_after_like?: "review" | "full_auto";
  paused?: boolean;
  min_fit?: number;
  hold_s?: number;
}): Promise<ApplySettings> =>
  request("/apply/settings", {
    method: "PUT",
    body: change,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => settingsSchema.parse(body),
  });

export const getCaps = (signal?: AbortSignal): Promise<Caps> =>
  request("/apply/caps", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => capsSchema.parse(body),
  });

/** The whole list, saved only if `revision` is still the one the page showed (else 409 stale). */
export const saveCaps = (items: CapDraft[], revision: string): Promise<Caps> =>
  request("/apply/caps", {
    method: "PUT",
    body: { items, revision },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => capsSchema.parse(body),
  });

export const getAnswerBank = (signal?: AbortSignal): Promise<Bank> =>
  request("/apply/answer-bank", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => bankSchema.parse(body),
  });

/** Explicitly save a reusable answer; the server derives its question key. */
export const addBankAnswer = (
  question: string,
  answer: string,
): Promise<Bank> =>
  request("/apply/answer-bank", {
    method: "POST",
    body: { question, answer },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => bankSchema.parse(body),
  });

/** One saved answer changed; `null` forgets it. */
export const saveBankAnswer = (
  questionKey: string,
  answer: string | null,
): Promise<Bank> =>
  request("/apply/answer-bank", {
    method: "PUT",
    body: { question_key: questionKey, answer },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => bankSchema.parse(body),
  });

/** The `{detail, code}` refusal of a settings route (409 or 422), or undefined for anything else. */
export function settingsRefusalOf(error: unknown): SettingsRefusal | undefined {
  if (!isApiError(error) || error.kind !== "http") return undefined;
  const parsed = refusalSchema.safeParse(error.body);
  return parsed.success ? parsed.data : undefined;
}

/** "Example Robotics: at most 3 applications in 90 days, and 180 days before the same role". */
export function capSentence(cap: CapDraft): string {
  const count =
    cap.max_count === 1 ? "1 application" : `${cap.max_count} applications`;
  const role = cap.same_role_days
    ? `, and ${days(cap.same_role_days)} before the same role`
    : "";
  return `${cap.label}: at most ${count} in ${days(cap.window_days)}${role}`;
}

export const days = (n: number) => (n === 1 ? "1 day" : `${n} days`);

/** "15 minutes" for 900 seconds; a length that is not whole minutes stays in seconds. */
export function holdText(seconds: number): string {
  if (seconds % 60 !== 0) return `${seconds} seconds`;
  const minutes = seconds / 60;
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/** A whole fit score from 0 to 100, or null. */
export function readFit(text: string): number | null {
  const digits = text.trim();
  if (!/^[0-9]{1,3}$/.test(digits)) return null;
  const value = Number(digits);
  return value <= 100 ? value : null;
}

/** A saved answer as text: most are text already, a yes or no reads Yes or No. */
export function answerText(answer: unknown): string {
  if (typeof answer === "string") return answer;
  if (typeof answer === "boolean") return answer ? "Yes" : "No";
  if (answer === null || answer === undefined) return "";
  return JSON.stringify(answer);
}

export type CapProblems = Partial<
  Record<"label" | "max_count" | "window_days" | "same_role_days", string>
>;

const whole = (text: string, low: number, high: number): number | null => {
  const digits = text.trim();
  if (!/^[0-9]{1,6}$/.test(digits)) return null;
  const value = Number(digits);
  return value >= low && value <= high ? value : null;
};

/** The draft of the form, or the sentence of each field the api would refuse. */
export function readCap(fields: {
  label: string;
  max_count: string;
  window_days: string;
  same_role_days: string;
}): { draft: CapDraft } | { problems: CapProblems } {
  const label = fields.label.split(/\s+/).filter(Boolean).join(" ");
  const maxCount = whole(fields.max_count, 1, COUNT_MAX);
  const windowDays = whole(fields.window_days, 1, DAYS_MAX);
  const roleBlank = fields.same_role_days.trim() === "";
  const roleDays = roleBlank ? null : whole(fields.same_role_days, 1, DAYS_MAX);
  const problems: CapProblems = {};
  if (!/[\p{L}\p{N}]/u.test(label))
    problems.label = "Name the company with letters or digits.";
  else if (label.length > LABEL_MAX)
    problems.label = `Keep the name to ${LABEL_MAX} characters.`;
  if (maxCount === null)
    problems.max_count = `Type a whole number from 1 to ${COUNT_MAX}.`;
  if (windowDays === null)
    problems.window_days = `Type a whole number of days from 1 to ${DAYS_MAX}.`;
  if (!roleBlank && roleDays === null)
    problems.same_role_days = `Type a whole number of days from 1 to ${DAYS_MAX}, or leave it empty.`;
  if (maxCount === null || windowDays === null || Object.keys(problems).length)
    return { problems };
  return {
    draft: {
      label,
      max_count: maxCount,
      window_days: windowDays,
      same_role_days: roleDays,
    },
  };
}
