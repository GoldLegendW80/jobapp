import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getPlaces, placesKey } from "./places-api";

/** The places of the postings of one filter query; the previous map stays while a new one loads. */
export function usePlaces(apiQuery: string) {
  return useQuery({
    queryKey: placesKey(apiQuery),
    queryFn: ({ signal }) => getPlaces(apiQuery, signal),
    placeholderData: keepPreviousData,
  });
}
