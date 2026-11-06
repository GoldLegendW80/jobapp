import { useQuery } from "@tanstack/react-query";

import { getSkillsSpend, getSpend, spendKey } from "./spend-api";

/**
 * The counts and the spend at the threshold `fit`. The key carries the saved settings the api
 * counts from, so that a save reads them again.
 */
export function useSpend(
  fit: number,
  saved: {
    auto: boolean | undefined;
    minFit: number;
    dailyCeiling: string | undefined;
  },
  options: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: [
      ...spendKey(fit),
      saved.auto,
      saved.minFit,
      saved.dailyCeiling,
    ] as const,
    queryFn: ({ signal }) => getSpend(fit, signal),
    ...options,
  });
}

/** What saving the skills `draft` (their JSON) would start; nothing is asked while it is null. */
export function useSkillsSpend(draft: string | null) {
  return useQuery({
    queryKey: ["documents", "spend", "skills", draft] as const,
    queryFn: ({ signal }) =>
      getSkillsSpend(JSON.parse(draft as string) as unknown, signal),
    enabled: draft !== null,
    retry: false,
    staleTime: 30_000,
  });
}

/** The counts and the spend at the saved threshold. */
export function useCurrentSpend() {
  return useQuery({
    queryKey: spendKey(),
    queryFn: ({ signal }) => getSpend(undefined, signal),
  });
}
