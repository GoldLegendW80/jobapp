import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { isApiError } from "@/lib/api";

import {
  createInvite,
  invitesKey,
  listInvites,
  revokeInvite,
  type InviteCreated,
  type NewInvite,
} from "./admin-api";

/** One page of the invite and reset links, from `offset`; the previous page shows meanwhile. */
export function useInvites(offset: number) {
  return useQuery({
    queryKey: [...invitesKey, offset],
    queryFn: ({ signal }) => listInvites(offset, signal),
    placeholderData: keepPreviousData,
  });
}

/** Creates an invite or reset link, then reads the list again. */
export function useCreateInvite(
  handlers: Pick<
    UseMutationOptions<InviteCreated, Error, NewInvite>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: NewInvite) => createInvite(body),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: invitesKey });
      return handlers.onSuccess?.(...args);
    },
  });
}

/** Revokes a link, then reads the list again; also when it was gone already (404). */
export function useRevokeInvite(
  handlers: Pick<
    UseMutationOptions<void, Error, string>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: invitesKey });
      return handlers.onSuccess?.(...args);
    },
    onError: (...args) => {
      const [failure] = args;
      if (isApiError(failure) && failure.status === 404)
        void client.invalidateQueries({ queryKey: invitesKey });
      return handlers.onError?.(...args);
    },
  });
}
