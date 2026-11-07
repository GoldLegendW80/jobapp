import { useQuery } from "@tanstack/react-query";

import { getProfile, profileKey } from "./profile-api";

/**
 * The person's profile, one cache entry for every page. An option left out keeps the query
 * client's default (it is spread, never passed as undefined).
 */
export function useProfile(
  options: { enabled?: boolean; retry?: boolean } = {},
) {
  return useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
    ...options,
  });
}
