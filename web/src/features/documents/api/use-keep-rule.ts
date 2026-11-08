import { useMutation, useQueryClient } from "@tanstack/react-query";

import { profileKey } from "@/features/profile/api/profile-api";

import { keepRule, type KeptRule } from "./writing-rules-api";

/** Keeps a rule from one of the person's chat messages; the profile is read again after it. */
export function useKeepRule(
  documentId: string,
  on: { onKept: (kept: KeptRule) => void },
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ messageId, text }: { messageId: number; text: string }) =>
      keepRule(documentId, messageId, text),
    onSuccess: (kept) => {
      void client.invalidateQueries({ queryKey: profileKey });
      on.onKept(kept);
    },
  });
}
