/**
 * Calls of /api/jobs/boards (api/app/jobs/boards/router.py): the starter list of company job
 * boards, the ATS and id of a pasted board address, and boards suggested from the person's own
 * postings. The list itself is saved with the search section (`company_boards`).
 */
import { z } from "zod";

import { request } from "@/lib/api";
import type { CompanyBoard } from "@/features/profile/api";

export const ATS_NAMES: Record<CompanyBoard["ats"], string> = {
  ashby: "Ashby",
  greenhouse: "Greenhouse",
  lever: "Lever",
  smartrecruiters: "SmartRecruiters",
  workable: "Workable",
  recruitee: "Recruitee",
};

/** schemas.py COMPANY_BOARDS_MAX */
export const BOARDS_MAX = 100;

const boardSchema = z.object({
  company: z.string(),
  ats: z.enum([
    "ashby",
    "greenhouse",
    "lever",
    "smartrecruiters",
    "workable",
    "recruitee",
  ]),
  board: z.string(),
  enabled: z.boolean(),
  url: z.string(),
});
export type BoardOut = z.infer<typeof boardSchema>;

const starterSchema = z.object({
  checked_on: z.string(),
  boards: z.array(boardSchema),
});
const suggestionsSchema = z.object({
  boards: z.array(boardSchema.extend({ postings: z.number().int() })),
});
export type Suggestion = z.infer<typeof suggestionsSchema>["boards"][number];

export const starterKey = ["jobs", "boards", "starter"] as const;
export const suggestionsKey = ["jobs", "boards", "suggestions"] as const;

export function getStarterBoards(signal?: AbortSignal) {
  return request("/jobs/boards/starter", {
    signal,
    validate: (data) => starterSchema.parse(data),
  });
}

export function detectBoard(url: string): Promise<BoardOut> {
  return request("/jobs/boards/detect", {
    method: "POST",
    body: { url },
    validate: (data) => boardSchema.parse(data),
  });
}

export function getBoardSuggestions(signal?: AbortSignal) {
  return request("/jobs/boards/suggestions", {
    signal,
    validate: (data) => suggestionsSchema.parse(data),
  });
}

/** The board as saved in the search section. */
export function savedBoard(board: BoardOut): CompanyBoard {
  return {
    company: board.company,
    ats: board.ats,
    board: board.board,
    enabled: board.enabled,
  };
}

/** Its identity, as the api compares boards: the ATS and the id, case aside. */
export function boardKey(board: Pick<CompanyBoard, "ats" | "board">): string {
  return `${board.ats}:${board.board.toLowerCase()}`;
}

/** The public board page, for a link. */
export function boardPage(board: Pick<CompanyBoard, "ats" | "board">): string {
  const id = encodeURIComponent(board.board);
  switch (board.ats) {
    case "ashby":
      return `https://jobs.ashbyhq.com/${id}`;
    case "greenhouse":
      return `https://job-boards.greenhouse.io/${id}`;
    case "lever":
      return `https://jobs.lever.co/${id}`;
    case "smartrecruiters":
      return `https://careers.smartrecruiters.com/${id}`;
    case "workable":
      return `https://apply.workable.com/${id}/`;
    case "recruitee":
      return `https://${id}.recruitee.com/`;
  }
}
