import { useQuery } from "@tanstack/react-query";

import { getSweep, listSweeps, sweepsKey } from "./sweeps-api";

/** The last sweeps and the automatic schedule (GET /api/jobs/sweeps). */
export function useSweeps(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: sweepsKey,
    queryFn: ({ signal }) => listSweeps(signal),
    ...options,
  });
}

/** Reads one past sweep with its details, afresh at each call, outside the cache. */
export function useFetchSweep() {
  return (id: string) => getSweep(id);
}
