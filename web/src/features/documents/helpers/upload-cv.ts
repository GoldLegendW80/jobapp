/** The words and rules of the CV upload: which files it takes, and when an upload is read. */
import type { IngestionDocument } from "@/features/ingestion/api/documents-api";

/** How often the uploaded file is read again while ingestion reads its text. */
export const READING_POLL_MS = 1500;
/** The review of what a profile run proposes. */
export const FILL_REVIEW = "/profile/fill?step=review";
export const NOT_A_CV = "Choose a PDF or a Word document (DOCX).";
export const READING_FOR_PROFILE =
  "Reading your CV for your profile. The review opens by itself when it ends.";
export const REVIEW_OPENED =
  "The reading of your CV ended. Here is what it proposes for your profile.";
export const RUN_CONSENT_FAILED =
  "Your consent to complete your profile could not be recorded. The app asks again when you start.";

/** What the file input accepts: a PDF or a Word document. */
export const CV_FILE_TYPES =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export const isCvFile = (name: string): boolean => /\.(pdf|docx)$/i.test(name);

/** The upload holds what may be a password or a key, and the person has not kept it yet. */
export const needsKeeping = (file: IngestionDocument): boolean =>
  file.secret_warning && file.secret_confirmed_at === null;

/** Ingestion has read the upload's text, and nothing waits for the person: it can be converted. */
export const isRead = (file: IngestionDocument | undefined): boolean =>
  file !== undefined &&
  file.status !== "pending" &&
  file.status !== "failed" &&
  !needsKeeping(file);
