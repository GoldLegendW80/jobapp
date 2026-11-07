import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import {
  action,
  getRun,
  runKey,
  startRun,
  type ProfileRun,
} from "./site-profile-api";

// While the run works or waits, it is read again this often.
const RUN_POLL_MS = 2000;

/** One run completing the person's profile on a site, read again while it works or waits. */
export function useSiteProfileRun(id: string) {
  return useQuery({
    queryKey: runKey(id),
    queryFn: ({ signal }) => getRun(id, signal),
    refetchInterval: (current) =>
      ["queued", "filling", "saving", "ready", "needs_person"].includes(
        current.state.data?.state ?? "",
      )
        ? RUN_POLL_MS
        : false,
  });
}

/** `update` keeps the run an answer returns; `refresh` reads the run again. */
export function useSiteProfileRunCache(id: string) {
  const client = useQueryClient();
  const update = async (run: ProfileRun) => {
    await client.cancelQueries({ queryKey: runKey(id) });
    client.setQueryData(runKey(id), run);
  };
  const refresh = () => {
    void client.invalidateQueries({ queryKey: runKey(id) });
  };
  return { update, refresh };
}

/** An action of the whole run by name (`confirm-autosave`, `cancel`); its answer is kept. */
export function useSiteProfileAction(id: string) {
  const { update } = useSiteProfileRunCache(id);
  return useMutation({
    mutationFn: (name: string) => action(id, name),
    onSuccess: update,
  });
}

/** Starts completing the person's profile on the site at `url`. */
export function useStartSiteProfileRun(
  url: string,
  handlers: Pick<UseMutationOptions<ProfileRun>, "onSuccess">,
) {
  return useMutation({ mutationFn: () => startRun(url), ...handlers });
}
