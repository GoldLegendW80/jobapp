import { useQuery } from "@tanstack/react-query";

import { coverageKey, getCoverage } from "./coverage-api";

/** The keyword coverage of a posting's CV at this revision of its draft. */
export function useCoverage(
  documentId: string,
  revision: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: coverageKey(documentId, revision),
    queryFn: ({ signal }) => getCoverage(documentId, signal),
    enabled,
  });
}
