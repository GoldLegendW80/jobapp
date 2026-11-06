import { useQueryClient } from "@tanstack/react-query";

import { describeError } from "@/lib/api";

import { applicationsKey, refusalOf } from "./apply-api";
import { likePosting, queueKey } from "./queue-api";

/** What a run of Likes did: how many went, how many in Full auto and rehearsing, and each refusal's count by sentence. */
export interface Likes {
  liked: number;
  fullAuto: number;
  rehearsals: number;
  refused: Map<string, number>;
}

/**
 * Likes the postings one after the other; a refusal keeps the api's sentence and the others
 * still go. Each liked posting's application and then the queue are read again.
 */
export function useLikePostings() {
  const queryClient = useQueryClient();
  return async (ids: readonly string[]): Promise<Likes> => {
    let liked = 0;
    let fullAuto = 0;
    let rehearsals = 0;
    const refused = new Map<string, number>();
    for (const postingId of ids) {
      try {
        const done = await likePosting(postingId);
        liked += 1;
        if (done.application.mode === "full_auto") fullAuto += 1;
        if (done.application.mode === "full_auto" && done.application.dry_run)
          rehearsals += 1;
        void queryClient.invalidateQueries({
          queryKey: applicationsKey(postingId),
        });
      } catch (error) {
        const why = refusalOf(error)?.detail ?? describeError(error);
        refused.set(why, (refused.get(why) ?? 0) + 1);
      }
    }
    void queryClient.invalidateQueries({ queryKey: queueKey });
    return { liked, fullAuto, rehearsals, refused };
  };
}
