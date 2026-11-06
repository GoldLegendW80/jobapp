import { useQuery } from "@tanstack/react-query";

import { recentApplications, recentApplicationsKey } from "./apply-api";
import { pollInterval } from "../helpers/apply-state";

/** The person's newest applications, read again every 5 seconds while one of them is moving. */
export function useRecentApplications() {
  return useQuery({
    queryKey: recentApplicationsKey,
    queryFn: ({ signal }) => recentApplications(signal),
    refetchInterval: (current) =>
      (current.state.data ?? []).some((item) => pollInterval(item) !== false)
        ? 5000
        : false,
  });
}
