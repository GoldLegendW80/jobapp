import { useQuery } from "@tanstack/react-query";

import type { CompanyBoard } from "@/features/profile/api/profile-api";

import {
  detectBoard,
  getBoardSuggestions,
  getStarterBoards,
  savedBoard,
  starterKey,
  suggestionsKey,
} from "./boards-api";
import { boardIdeas } from "../helpers/source-ideas";

/** The starter list of company boards, offered as ideas (never saved by itself). */
export function useStarterBoards() {
  return useQuery({
    queryKey: starterKey,
    queryFn: ({ signal }) => getStarterBoards(signal),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
}

/** The company boards to offer: those of the person's own postings and the popular ones, none
 * already in `boards`. `pending` while either list loads. */
export function useBoardIdeas(boards: readonly CompanyBoard[] | null) {
  const starter = useStarterBoards();
  const suggestions = useQuery({
    queryKey: suggestionsKey,
    queryFn: ({ signal }) => getBoardSuggestions(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  return {
    pending: starter.isPending || suggestions.isPending,
    ...boardIdeas(
      boards,
      (suggestions.data?.boards ?? []).map(savedBoard),
      (starter.data?.boards ?? []).map(savedBoard),
    ),
  };
}

/** Asks the api which job board an address is, by its ATS and id; a 422 means none it reads. */
export function useDetectBoard() {
  return (url: string) => detectBoard(url);
}
