import { useQuery } from "@tanstack/react-query";

import { getLinkedIn, linkedinKey } from "./linkedin-api";

/** LinkedIn's switch and the person's acceptance of its sentence (GET /api/browser/linkedin). */
export function useLinkedIn(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: linkedinKey,
    queryFn: ({ signal }) => getLinkedIn(signal),
    ...options,
  });
}
