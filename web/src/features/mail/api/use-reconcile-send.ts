import { useMutation, useQueryClient } from "@tanstack/react-query";

import { reconcileSend } from "./sends-api";

/** "Check Sent" of one send; what it finds may change any page, so every read is made again. */
export function useReconcileSend(sendId: string | null | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => reconcileSend(sendId),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
}
