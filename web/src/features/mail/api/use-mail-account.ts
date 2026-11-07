import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  disconnectMail,
  getMailAccount,
  mailAccountQueryKey,
  saveConsent,
  type Disconnected,
} from "./account-api";

/** The person's Gmail connection (GET /api/mail/account). */
export function useMailAccount() {
  return useQuery({
    queryKey: mailAccountQueryKey,
    queryFn: ({ signal }) => getMailAccount(signal),
  });
}

/** Grant or withdraw the consent of `version`; the account it answers becomes the cached one. */
export function useSaveMailConsent(version: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (granted: boolean) => saveConsent({ granted, version }),
    onSuccess: (saved) => {
      client.setQueryData(mailAccountQueryKey, saved);
    },
  });
}

/** Disconnect Gmail, then read the account again. */
export function useDisconnectMail(
  handlers: Pick<UseMutationOptions<Disconnected>, "onSuccess" | "onError">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: disconnectMail,
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: mailAccountQueryKey });
      return handlers.onSuccess?.(...args);
    },
  });
}
