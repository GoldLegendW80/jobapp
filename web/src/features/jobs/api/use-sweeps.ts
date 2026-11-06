import { useQuery } from "@tanstack/react-query";

import { listSweeps, sweepsKey } from "./sweeps-api";

/** The last sweeps and the automatic schedule (GET /api/jobs/sweeps). */
export function useSweeps(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: sweepsKey,
    queryFn: ({ signal }) => listSweeps(signal),
    ...options,
  });
}
