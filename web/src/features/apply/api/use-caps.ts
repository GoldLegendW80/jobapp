import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  capsKey,
  getCaps,
  saveCaps,
  settingsRefusalOf,
  type CapDraft,
  type Caps,
} from "./settings-api";

/** The person's company caps, with what each counts now. */
export function useCaps() {
  return useQuery({
    queryKey: capsKey,
    queryFn: ({ signal }) => getCaps(signal),
  });
}

type CapsChange = { items: CapDraft[]; revision: string };

/**
 * Saves the whole list with the revision the page showed: the list it answers becomes the cached
 * one, and a list changed elsewhere (`stale`) is read again.
 */
export function useSaveCaps<Change extends CapsChange>(
  handlers: Pick<
    UseMutationOptions<Caps, Error, Change>,
    "onMutate" | "onSuccess" | "onError"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ items, revision }: Change) => saveCaps(items, revision),
    ...handlers,
    onSuccess: (...args) => {
      client.setQueryData<Caps>(capsKey, args[0]);
      return handlers.onSuccess?.(...args);
    },
    onError: (...args) => {
      if (settingsRefusalOf(args[0])?.code === "stale")
        void client.invalidateQueries({ queryKey: capsKey });
      return handlers.onError?.(...args);
    },
  });
}
