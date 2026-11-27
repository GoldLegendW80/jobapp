import { useQuery } from "@tanstack/react-query";

import { sharedWindow } from "../helpers/criteria-words";
import { listSearches, searchesKey } from "./searches-api";

/** The age window a badge names (criteria-words.ts `sharedWindow`), read from the person's
 * searches when `enabled` (a badge that says `older`); null while they load, when they cannot be
 * read or when their windows differ. */
export function useCriteriaWindow(enabled: boolean): number | null {
  const searches = useQuery({
    queryKey: searchesKey,
    queryFn: ({ signal }) => listSearches(signal),
    enabled,
    staleTime: 60_000,
    select: (list) => sharedWindow(list.items),
  });
  return searches.data ?? null;
}
