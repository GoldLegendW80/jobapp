import { useQuery } from "@tanstack/react-query";

import { getOnboarding, onboardingKey } from "./onboarding-api";

/** Where the person stands in the first login wizard. */
export function useOnboarding(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => getOnboarding(signal),
    ...options,
  });
}
