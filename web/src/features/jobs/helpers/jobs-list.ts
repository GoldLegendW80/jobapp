/** The Jobs list's measures, the posting it shows beside it, and the words of an item. Pure. */
import { WORK_MODE_WORDS, contractWord, readableCode } from "@/lib/wording";

import type { JobListItem } from "../api/jobs-api";

/** Items rendered beyond the visible ones, above and below. */
export const OVERSCAN = 8;
/** Start fetching the next page when the view gets this close to the last loaded item. */
export const PREFETCH_ROWS = 20;
/** The height of an item before it is measured: a title on one line, the four lines under it. */
export const ITEM_ESTIMATE = 132;
/** The space between two items, and above the first and under the last. */
export const ITEM_GAP = 4;
export const LIST_PADDING = 8;

/** The posting the detail pane shows, and how the list changes it (the URL's `job`). */
export interface ListCurrent {
  id: string | null;
  onSelect: (id: string) => void;
}

/** "Lyon, France, Hybrid, Permanent": the place, the work mode unless the place says it, the contract. */
export function placeLine(job: JobListItem): string {
  const mode = job.work_mode
    ? (WORK_MODE_WORDS[job.work_mode] ?? readableCode(job.work_mode))
    : null;
  const parts = [job.location.trim()];
  if (mode && !job.location.toLowerCase().includes(mode.toLowerCase()))
    parts.push(mode);
  parts.push(contractWord(job.contract_type));
  return parts.filter(Boolean).join(", ");
}

/** The company's first letter or digit, for its avatar. */
export function initialOf(company: string): string {
  return (/[\p{L}\p{N}]/u.exec(company)?.[0] ?? "?").toUpperCase();
}
