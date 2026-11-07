import { useMutation, useQueryClient } from "@tanstack/react-query";

import { baseCvsKey, type BaseCvs } from "./base-cvs-api";
import { captureDesign, documentKey, type Generation } from "./documents-api";

/**
 * Starts the design capture of a base CV again. The capture it answers goes into the cached list;
 * after either outcome the list and the CV's document are read again, and `onSettled` gets the
 * capture that refresh left in the cache.
 */
export function useRetryDesignCapture(
  baseId: string,
  on: { onSettled: (capture: Generation | null) => void },
) {
  const client = useQueryClient();
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: baseCvsKey }),
      client.invalidateQueries({ queryKey: documentKey(baseId) }),
    ]);
  return useMutation({
    mutationFn: () => captureDesign(baseId),
    onSuccess: (capture) => {
      client.setQueryData<BaseCvs>(baseCvsKey, (list) =>
        list === undefined
          ? list
          : {
              ...list,
              base_cvs: list.base_cvs.map((one) =>
                one.id === baseId
                  ? { ...one, design_generation: capture }
                  : one,
              ),
            },
      );
    },
    onSettled: async () => {
      try {
        await refresh();
      } finally {
        const list = client.getQueryData<BaseCvs>(baseCvsKey);
        const mine = list?.base_cvs.find((one) => one.id === baseId);
        on.onSettled(mine?.design_generation ?? null);
      }
    },
  });
}
