/** The CV tab's address, limits and the sentence of what a base CV is doing. */
import type { BaseCv } from "../api/base-cvs-api";
import { failedSentence, isOpen, WAIT_SENTENCES } from "./labels";

/** The list of the CV tab; one CV opens at `${CV_LIST_PATH}/<id>`. */
export const CV_LIST_PATH = "/profile/cv";
export const CV_NAME_MAX = 100;

export const tooManyCvs = (max: number) =>
  `You have ${String(max)} CVs, the most the app keeps. Delete one to add another.`;

/** What a CV is doing, in a sentence; null when it is simply there. */
export function cvStateLine(cv: BaseCv, only: boolean): string | null {
  const generation = cv.generation;
  if (generation === null) return null;
  if (generation.status === "waiting")
    return generation.wait_reason
      ? WAIT_SENTENCES[generation.wait_reason]
      : "Waiting to start.";
  if (isOpen(generation))
    return cv.last_version_at === null && only
      ? "Your first CV is being built."
      : cv.last_version_at === null
        ? "Being built."
        : "Writing a new version.";
  if (generation.status === "failed") return failedSentence(generation.error);
  return null;
}
