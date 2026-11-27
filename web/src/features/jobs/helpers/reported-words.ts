/**
 * Jobs J30 (#829, "true numbers"): what happened to the postings a source of a search reported,
 * in sentences, from the counters the api serves (api/app/jobs/schemas.py SourceOut, SweepOut). A
 * figure that is zero or absent is not said. The assistant's own count of the results it read is
 * said as its words, never as a fact of the app. A sweep from before J30 is said the same way, from
 * the figures it has (`found`, `new`, and the sweep's `duplicates`).
 */
import { plural } from "./selection";

/** Why the app's check refused a posting an assistant reported, by the api's reason
 * (api/app/browser/finder.py `REPORT_ADVICE`), as the end of a sentence. */
export const REFUSAL_REASONS: Record<string, string> = {
  title_not_on_page: "its title was not on its page",
  not_on_opened_page:
    "the app opened its page and did not find its title there (a security check or a sign in wall may have shown)",
  detail_not_opened: "the assistant had not opened its page",
  not_seen: "its address was on no page the assistant read",
  page_error: "its page answered an error",
  not_a_posting_page: "it was the website's start page, not a posting",
  app_opens_max: "it was one page more than the app opens for one report",
  duplicate: "it was reported twice",
  other_host: "its address was on another website",
  not_an_address: "it had no address the app could open",
  missing_field: "its title, address or text was missing",
  field_too_long: "its title or text was too long",
  title_too_long: "its title was longer than its page shows",
  not_a_posting: "it was not given as a posting",
  postings_max: "the report held more postings than the app takes at once",
  unsupported_interaction: "the app refused the assistant's action",
};

export const OTHER_REFUSAL = "the app's check found another problem";

export interface ReportedFigures {
  found?: number;
  new?: number;
  skipped_by_rule?: number;
  duplicates?: number;
  outside?: number;
  unwanted?: number;
  refused?: Record<string, number>;
  opened_by_app?: number;
  results_read?: number | null;
  other_contract?: number;
}

const positive = (n: number | null | undefined): n is number =>
  typeof n === "number" && n > 0;

/** "3 refused by the app's check: 2 because its title was not on its page; 1 because ...". */
export function refusedSentence(refused?: Record<string, number>): string {
  const reasons = Object.entries(refused ?? {}).filter(([, n]) => n > 0);
  if (!reasons.length) return "";
  const total = reasons.reduce((sum, [, n]) => sum + n, 0);
  const why = reasons
    .map(
      ([reason, n]) =>
        `${n.toLocaleString("en-GB")} because ${
          Object.hasOwn(REFUSAL_REASONS, reason)
            ? REFUSAL_REASONS[reason]
            : OTHER_REFUSAL
        }`,
    )
    .join("; ");
  return `${total.toLocaleString("en-GB")} refused by the app's check: ${why}.`;
}

/** The new postings kept, of them those outside the criteria, and those already in the list. */
export function keptParts(figures: ReportedFigures): string[] {
  const parts: string[] = [];
  if (positive(figures.new)) {
    const kept = `${plural(figures.new, "new posting")} kept`;
    parts.push(
      positive(figures.outside)
        ? `${kept}, ${figures.outside.toLocaleString("en-GB")} of them outside your criteria`
        : kept,
    );
  }
  if (positive(figures.duplicates))
    parts.push(
      `${figures.duplicates.toLocaleString("en-GB")} already in your list`,
    );
  return parts;
}

/** An older sweep's postings of a contract the person did not choose, left out before J30. */
export function otherContractSentence(figures: ReportedFigures): string {
  return positive(figures.other_contract)
    ? `${plural(figures.other_contract, "posting")} of a contract you did not choose left out.`
    : "";
}

/** What a source of a search did with what it reported, in sentences (its line in the summary). */
export function reportedSentences(figures: ReportedFigures): string {
  const head = positive(figures.found)
    ? `${plural(figures.found, "posting")} found`
    : "";
  const kept = keptParts(figures);
  if (positive(figures.skipped_by_rule))
    kept.push(
      `${figures.skipped_by_rule.toLocaleString("en-GB")} set to Skipped by a never apply rule`,
    );
  const first = head
    ? `${head}${kept.length ? `: ${kept.join("; ")}` : ", none kept"}.`
    : kept.length
      ? `${kept.join("; ")}.`
      : "No posting kept.";
  const more = [
    positive(figures.unwanted)
      ? `${plural(figures.unwanted, "posting")} set aside by your unwanted titles and places.`
      : "",
    refusedSentence(figures.refused),
    positive(figures.opened_by_app)
      ? `The app opened ${plural(figures.opened_by_app, "posting")} itself, as the assistant had not.`
      : "",
    positive(figures.results_read)
      ? `The assistant says it read ${plural(figures.results_read, "result")}.`
      : "",
    otherContractSentence(figures),
  ];
  return [first, ...more].filter((part) => part !== "").join(" ");
}
