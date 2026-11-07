/** What a decision on proposals did, or why the server refused it, in the person's words. */
import { describeError, errorDetail, isApiError } from "@/lib/api";

import type {
  Additions,
  ProposalItem,
  ProposalSection,
  SectionDecided,
} from "../api/proposals-api";
import { isRecord, refusedField } from "./proposal-value";
import { plural, sectionName } from "./proposal-words";

/** Why "accept all" left a proposal for the user to accept on its own (the server's reasons). */
const ONE_BY_ONE = new Set([
  "locked",
  "flagged",
  "removal",
  "replace",
  "stale",
  "typed_by_you",
]);

/** What the page says when the server refuses a decision: its own sentence when it gave one. */
export function refusal(error: unknown): string {
  if (isApiError(error) && error.status === 404)
    return "This proposal is no longer there.";
  const detail = errorDetail(error);
  if (detail && isApiError(error) && error.status === 422)
    return refusedField(detail)?.message ?? detail;
  if (detail && isApiError(error) && error.status === 409) return detail;
  return describeError(error);
}

/** The proposal a refused bulk accept names (`proposal_id` of its 409), if any. */
export function stoppedBy(error: unknown): string | undefined {
  if (!isApiError(error) || error.status !== 409 || !isRecord(error.body))
    return undefined;
  const id = error.body.proposal_id;
  return typeof id === "string" ? id : undefined;
}

/** "Accepted 12. 3 still need your decision.", and what stopped a section, in its words. */
export function additionsOutcome(done: Additions): string {
  return [
    `Accepted ${String(done.decided.length)}.`,
    done.pending === 0
      ? "Nothing else needs your decision."
      : `${plural(done.pending, "proposal still needs", "proposals still need")} your decision.`,
    ...done.stopped.map((one) => `${sectionName(one.section)}: ${one.detail}`),
  ].join(" ");
}

/** What a bulk decision did, from the server's answer: what it decided and what it left. */
export function outcomeOf(
  done: SectionDecided,
  what: "accept" | "reject",
): string {
  const count = done.decided.length;
  const oneByOne = done.skipped.filter((one) =>
    ONE_BY_ONE.has(one.reason),
  ).length;
  const gone = done.skipped.length - oneByOne;
  return [
    `${what === "reject" ? "Rejected" : "Accepted"} ${String(count)}.`,
    oneByOne > 0
      ? `${String(oneByOne)} left for you to accept one by one.`
      : "",
    gone > 0
      ? `${plural(gone, "was", "were")} decided elsewhere meanwhile or no longer here.`
      : "",
  ]
    .filter((part) => part !== "")
    .join(" ");
}

/** What the bulk decisions of a section may take: only the proposals on the screen. */
export interface SectionShown {
  /** The proposals the page shows. */
  listed: ProposalItem[];
  pending: ProposalItem[];
  /** Everything pending but what the page already shows as not fitting. */
  asked: ProposalItem[];
  /** How many of `asked` a bulk accept takes; the server leaves out the others and says so. */
  acceptable: number;
  /** Pending proposals of the section beyond the page: the bulk decisions leave them. */
  behind: number;
}

export function sectionShown(
  section: ProposalSection,
  shown: number,
): SectionShown {
  const listed = section.items.slice(0, shown);
  const pending = listed.filter((item) => item.status === "pending");
  const asked = pending.filter(
    (item) => item.bulk !== "invalid" && item.bulk !== "profile_conflict",
  );
  return {
    listed,
    pending,
    asked,
    acceptable: asked.filter((item) => item.bulk === null).length,
    behind: section.items
      .slice(shown)
      .filter((item) => item.status === "pending").length,
  };
}
