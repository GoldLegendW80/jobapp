import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { listUsers, usersKey } from "./admin-api";

/** One page of the accounts, from `offset`; the previous page shows meanwhile. */
export function useUsers(offset: number) {
  return useQuery({
    queryKey: [...usersKey, offset],
    queryFn: ({ signal }) => listUsers(offset, signal),
    placeholderData: keepPreviousData,
  });
}
