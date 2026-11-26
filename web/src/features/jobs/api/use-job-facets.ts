import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { toSearchParams, type JobFilters } from "../helpers/filters";
import { jobFacetsKey, listFacets } from "./jobs-api";

/** The places and the websites the Filters panel offers, under the other filters. */
export function useJobFacets(filters: JobFilters, enabled: boolean) {
  const query = toSearchParams(filters, { withSort: false }).toString();
  return useQuery({
    queryKey: jobFacetsKey(query),
    queryFn: ({ signal }) => listFacets(query, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}
