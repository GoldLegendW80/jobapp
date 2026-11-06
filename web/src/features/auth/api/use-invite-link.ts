import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { acceptInvite, checkInvite, type User } from "./auth-api";

const linkQueryKey = (token: string) => ["link", token] as const;

/** What an invite or reset link is (GET /auth/invites/{token}), read once and never kept. */
export function useInviteLink(token: string, enabled: boolean) {
  return useQuery({
    queryKey: linkQueryKey(token),
    queryFn: ({ signal }) => checkInvite(token, signal),
    enabled,
    staleTime: Infinity,
    gcTime: 0,
  });
}

/** Accept the link with a password (and a name for an invite); the link is then forgotten. */
export function useAcceptInvite(
  token: string,
  handlers: Pick<
    UseMutationOptions<User, Error, { name?: string; password: string }>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; password: string }) =>
      acceptInvite(token, body),
    ...handlers,
    onSuccess: (...args) => {
      client.removeQueries({ queryKey: ["link"] });
      return handlers.onSuccess?.(...args);
    },
  });
}
