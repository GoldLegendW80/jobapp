import { QueryClient } from "@tanstack/react-query";

/**
 * Defaults chosen so a failing api never causes a request storm: no automatic retries and no
 * refetch on focus or reconnect. The user retries explicitly.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        staleTime: 30_000,
      },
    },
  });
}
