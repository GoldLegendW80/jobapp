/** The person's searches (searches-api.ts) and what each row of Your searches does with them. */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { copyName } from "../helpers/saved-searches";
import {
  bodyOf,
  changeSearch,
  createSearch,
  deleteSearch,
  listSearches,
  runAllSearches,
  runSearch,
  searchesKey,
  takeBackRun,
  type SavedSearch,
  type SearchRun,
} from "./searches-api";
import { sweepsKey } from "./sweeps-api";

export function useSavedSearches(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: searchesKey,
    queryFn: ({ signal }) => listSearches(signal),
    ...options,
  });
}

/** The newest list, read again: what a save checks the other searches against. */
export function useReadSearches() {
  const client = useQueryClient();
  return () =>
    client.query({
      queryKey: searchesKey,
      queryFn: ({ signal }) => listSearches(signal),
      staleTime: 0,
    });
}

type Handlers<T, V> = Pick<
  UseMutationOptions<T, Error, V>,
  "onSuccess" | "onError"
>;

function useRefreshing<T, V>(
  mutationFn: (value: V) => Promise<T>,
  handlers: Handlers<T, V>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: searchesKey });
      void client.invalidateQueries({ queryKey: sweepsKey });
      return handlers.onSuccess?.(...args);
    },
    onError: (...args) => {
      void client.invalidateQueries({ queryKey: searchesKey });
      return handlers.onError?.(...args);
    },
  });
}

export function useRunSearch(handlers: Handlers<SearchRun, string>) {
  return useRefreshing(runSearch, handlers);
}

export function useRunAll(handlers: Handlers<SearchRun[], void>) {
  return useRefreshing(() => runAllSearches(), handlers);
}

export function useTakeBack(handlers: Handlers<void, string>) {
  return useRefreshing(takeBackRun, handlers);
}

export function useDeleteSearch(handlers: Handlers<void, string>) {
  return useRefreshing(deleteSearch, handlers);
}

/** A copy of a search beside it, named after it, its schedule off. */
export function useDuplicateSearch(
  handlers: Handlers<SavedSearch, { search: SavedSearch; names: string[] }>,
) {
  return useRefreshing(
    ({ search, names }: { search: SavedSearch; names: string[] }) =>
      createSearch({
        ...bodyOf(search),
        name: copyName(search.name, names),
        schedule: { ...bodyOf(search).schedule, enabled: false },
      }),
    handlers,
  );
}

/** A new name, on the revision the row showed. */
export function useRenameSearch(
  handlers: Handlers<SavedSearch, { search: SavedSearch; name: string }>,
) {
  return useRefreshing(
    ({ search, name }: { search: SavedSearch; name: string }) =>
      changeSearch(search.id, search.revision, { ...bodyOf(search), name }),
    handlers,
  );
}
