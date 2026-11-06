import { useQuery } from "@tanstack/react-query";

import { getPrompt, promptKey } from "./prompt-api";

/** The prompt of step 1, which the person runs with their own assistant. */
export function usePrompt() {
  return useQuery({
    queryKey: promptKey,
    queryFn: ({ signal }) => getPrompt(signal),
    // The text does not change while the user reads it, and it holds their name: it is not
    // kept once the step is left.
    staleTime: Infinity,
    gcTime: 0,
  });
}
