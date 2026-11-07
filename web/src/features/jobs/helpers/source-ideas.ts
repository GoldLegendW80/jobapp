/**
 * What the list of addresses offers to add in one click: the examples under the address box and
 * the company job boards folded under "More company job boards". Pure: the editor's hooks load
 * the boards (use-board-ideas.ts).
 */
import type { CompanyBoard } from "@/features/profile/api/profile-api";

import { boardKey } from "../api/boards-api";
import { platformOf } from "./site-permissions";
import { EXAMPLES, listed, type Example, type SourcesDraft } from "./sources";

/** Whether a board example's address (jobs.ashbyhq.com/<id>, job-boards.greenhouse.io/<id>) is
 * already one of the company boards. */
function boardListed(boards: readonly CompanyBoard[] | null, url: string) {
  const match =
    /^https:\/\/(jobs\.ashbyhq\.com|job-boards\.greenhouse\.io)\/([^/]+)/.exec(
      url,
    );
  if (!match || !boards) return false;
  const ats = match[1] === "jobs.ashbyhq.com" ? "ashby" : "greenhouse";
  const key = boardKey({ ats, board: match[2] ?? "" });
  return boards.some((board) => boardKey(board) === key);
}

/** The examples the draft does not list yet; a known job website is offered while no address
 * of it is in the list. */
export function examplesToOffer(draft: SourcesDraft): Example[] {
  return EXAMPLES.filter((example) => {
    if (example.platform)
      return !draft.sites.some((site) => platformOf(site) === example.platform);
    return (
      !listed(draft, example.url) && !boardListed(draft.boards, example.url)
    );
  });
}

/** The boards of the person's own postings, then the popular ones, each without the boards
 * already in the list and the popular ones without those of her postings. */
export function boardIdeas(
  inList: readonly CompanyBoard[] | null,
  ofPostings: readonly CompanyBoard[],
  starter: readonly CompanyBoard[],
): { fromPostings: CompanyBoard[]; popular: CompanyBoard[] } {
  const listedKeys = new Set((inList ?? []).map(boardKey));
  const fromPostings = ofPostings.filter(
    (board) => !listedKeys.has(boardKey(board)),
  );
  const fromPostingsKeys = new Set(fromPostings.map(boardKey));
  const popular = starter.filter(
    (board) =>
      !listedKeys.has(boardKey(board)) &&
      !fromPostingsKeys.has(boardKey(board)),
  );
  return { fromPostings, popular };
}
