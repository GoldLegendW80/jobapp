import type { Approval, Refusal } from "../api/apply-api";

/** The address an e-mail application goes to, as the person last saved it on the form. */
export const emailToOf = (approval: Approval): string | undefined =>
  approval.answers.find((answer) => answer.field_key === "email_to")?.value as
    string | undefined;

/** A `stale` refusal of Approve carries the form as it is now; null for any other refusal. */
export function currentFormOf(
  refusal: Refusal | undefined,
): Pick<Approval, "answers_hash" | "answers"> | null {
  if (refusal?.code !== "stale" || !refusal.answers_hash || !refusal.answers)
    return null;
  return { answers_hash: refusal.answers_hash, answers: refusal.answers };
}
