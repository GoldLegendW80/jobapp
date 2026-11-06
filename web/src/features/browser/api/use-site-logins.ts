import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { browserQueryKey, startSession, type Session } from "./browser-api";
import {
  getSiteLogins,
  siteDone,
  siteLoginsQueryKey,
  type SiteDone,
} from "./site-logins-api";

/** The job websites the person's browser keeps a login of (GET /api/browser/sites). */
export function useSiteLogins() {
  return useQuery({
    queryKey: siteLoginsQueryKey,
    queryFn: ({ signal }) => getSiteLogins(signal),
  });
}

/** A website's Sign in: the person's own session at `url`, or a place in the queue. */
export function useStartSignIn(
  url: string,
  handlers: Pick<UseMutationOptions<Session>, "onSuccess">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => startSession("signin", url),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: browserQueryKey });
      return handlers.onSuccess?.(...args);
    },
  });
}

/** Done of a website's Sign in: ends the session and says whether the login was kept. */
export function useSiteDone(
  url: string,
  handlers: Pick<UseMutationOptions<SiteDone>, "onSuccess">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => siteDone(url),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: browserQueryKey });
      return handlers.onSuccess?.(...args);
    },
  });
}
