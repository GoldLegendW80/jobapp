import { useInfiniteQuery } from "@tanstack/react-query";

import type { SectionName } from "./profile-api";
import { listRevisions, revisionsKey } from "./revisions-api";

/** The saves of a section, newest first, a page at a time, for the History drawer. */
export function useSectionVersions(section: SectionName) {
  const query = useInfiniteQuery({
    queryKey: revisionsKey(section),
    queryFn: ({ pageParam, signal }) =>
      listRevisions(section, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
    // Saves happen in the tab next to it: always read the list afresh when the drawer opens.
    staleTime: 0,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return {
    query,
    items,
    newest: items[0]?.revision,
    moreError: query.isFetchNextPageError ? query.error : null,
  };
}

export type SectionVersions = ReturnType<typeof useSectionVersions>;
