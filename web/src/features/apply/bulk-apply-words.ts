/**
 * The words of Apply to the selected postings: what each posting's click writes first, what the
 * review box makes it do, and the summary after the click. No dashes as separators.
 */
import { estimateSentence } from "@/features/documents/labels";
import { plural } from "@/features/jobs/selection";

import type {
  BulkItem,
  BulkPosting,
  BulkPreview,
  BulkStarted,
} from "./bulk-apply-api";

/** "Example Robotics, Robotics Engineer", or what stands for a posting that is gone. */
export const nameOf = (posting: BulkPosting): string =>
  posting.company !== null && posting.title !== null
    ? `${posting.company}, ${posting.title}`
    : "A posting no longer in your list";

/** What the click does, by the person's "Let me review each filled form" box. */
export const bulkDoes = (reviewBeforeSend: boolean): string =>
  reviewBeforeSend
    ? "The assistant fills each form, then you check it and send it."
    : "The assistant fills each form and sends it.";

const WRITTEN: Record<string, string> = {
  "cv,letter": "CV and letter written first",
  cv: "CV written first",
  letter: "Letter written first",
};

/** Whether a posting's CV and letter are there, or what is written first and its estimate. */
export function documentsLine(item: BulkItem): string {
  const key = item.documents.join(",");
  const documents =
    key === ""
      ? "CV and letter ready."
      : `${WRITTEN[key] ?? "Documents written first"}${
          item.estimate ? `, ${estimateSentence(item.estimate)}` : ""
        }.`;
  return item.email
    ? `${documents} An e-mail application: you check it and send it in Applications.`
    : documents;
}

/** The line under the list when the click writes documents: the whole estimate. */
export const writingLine = (preview: BulkPreview): string | null =>
  preview.estimate
    ? `Writing the missing documents: ${estimateSentence(preview.estimate)} in all.`
    : null;

/** The postings past the limit, said in one sentence. */
export const beyondLine = (preview: BulkPreview): string | null =>
  preview.beyond.length > 0
    ? `Apply to the first ${String(preview.limit)}; select the others after. ${plural(preview.beyond.length, "posting")} ${preview.beyond.length === 1 ? "is" : "are"} not in this click.`
    : null;

/** "Applying to 12 postings. 3 were left out:", the first line of the summary. */
export function summaryLine(done: BulkStarted): string {
  const started =
    done.started > 0
      ? `Applying to ${plural(done.started, "posting")}.`
      : "Nothing was started.";
  if (done.left_out === 0) return started;
  return `${started} ${String(done.left_out)} ${done.left_out === 1 ? "was" : "were"} left out:`;
}

/** Each posting left out at the click, with its reason, named as the confirmation showed it. */
export function leftOutLines(done: BulkStarted, shown: BulkPreview): string[] {
  const names = new Map(shown.items.map((item) => [item.posting_id, item]));
  return done.results
    .filter((result) => !result.started)
    .map((result) => {
      const named = names.get(result.posting_id);
      const name = named ? nameOf(named) : "A posting";
      return `${name}: ${result.detail ?? "It was not started."}`;
    });
}
