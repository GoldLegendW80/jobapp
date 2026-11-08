/** The words and rules of the notes on an offer letter (SPEC-documents.md, "Reading an offer"). */
import type { OfferNotes } from "../api/offer-notes-api";

/** An offer comes after an interview: the Offer section shows from then on in the pipeline. */
export const OFFER_STATUSES: readonly string[] = [
  "interview",
  "rejected",
  "closed",
];

export const isOfferStage = (status: string) => OFFER_STATUSES.includes(status);

export const OFFER_FILE_TYPES =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const NOT_A_LETTER = "Choose a PDF or a Word document (DOCX).";
export const INTRO =
  "Upload the offer letter or the contract you received, as a PDF or a Word file. The assistant explains each clause in plain words, in the letter's language, says what differs from the posting and from the pay you seek, and lists questions for a lawyer. It gives no verdict.";
export const NOT_ADVICE =
  "These notes explain the letter. They are not legal advice.";
export const CHAT_INTRO =
  "Ask for a clause explained again, a question added for a lawyer or a shorter summary. The notes never say whether to sign.";
export const CHAT_EXAMPLES = [
  "Explain the trial period more simply",
  "Add a question about the notice period",
  "Make the summary shorter",
];

const STOPPED: Record<string, string> = {
  token_limit: "The reading stopped at its limit.",
  invalid_output: "The assistant could not write valid notes.",
};

/** Where the newest reading stands. */
export function readingState(
  notes: OfferNotes | undefined,
): "none" | "reading" | "failed" | "done" {
  const reading = notes?.reading ?? null;
  if (reading === null) return "none";
  if (["waiting", "queued", "running"].includes(reading.status))
    return "reading";
  return reading.status === "done" ? "done" : "failed";
}

/** Why the newest reading wrote nothing, as one sentence. */
export const stoppedSentence = (error: string | null) =>
  `${STOPPED[error ?? ""] ?? "The reading stopped before the notes were stored."} Read the letter again.`;

/** "Only the first 30,000 characters of the letter were read." */
export const truncatedSentence = (readChars: number) =>
  `Only the first ${readChars.toLocaleString("en-US")} characters of the letter were read.`;

/** "The posting stated 55,000 to 65,000 EUR a year. You seek 60,000 to 70,000 EUR a year." */
export function comparedWith(pay: OfferNotes["pay"]): string {
  const posting =
    pay.posting.pay === null
      ? "The posting stated no pay."
      : `The posting stated ${pay.posting.pay}.`;
  const sought =
    pay.pay_sought.length === 0
      ? "Your profile states no pay you seek."
      : `You seek ${pay.pay_sought.join(", or ")}.`;
  return `${posting} ${sought}`;
}
