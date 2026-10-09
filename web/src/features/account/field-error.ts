import { errorDetail } from "@/lib/api";

/**
 * The api's account errors name their field first: "current_password: The current password is
 * incorrect." Returns the field and the message for the person, when the field is one of
 * `fields`; undefined otherwise (then the whole detail is a form level error).
 */
export function fieldError<F extends string>(
  error: unknown,
  fields: readonly F[],
): { field: F; message: string } | undefined {
  const detail = errorDetail(error);
  if (!detail) return undefined;
  const match = /^([a-z_]+): (.+)$/s.exec(detail);
  const [, field, text] = match ?? [];
  if (!field || !text || !fields.includes(field as F)) return undefined;
  return { field: field as F, message: text.trim() };
}
