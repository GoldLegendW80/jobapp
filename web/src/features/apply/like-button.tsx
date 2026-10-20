import { useQueryClient } from "@tanstack/react-query";
import { Heart, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { plural } from "@/features/jobs/selection";
import { describeError } from "@/lib/api";

import { applicationsKey, refusalOf } from "./apply-api";
import { likePosting, queueKey } from "./queue-api";
import { LIKE_MAX } from "./queue-state";

/**
 * The Like of the jobs list's bulk bar, until the swipe deck (#74) sends Likes: one Like per
 * selected posting, one after the other, at most LIKE_MAX per click (the label says so when more
 * are selected). A Like is the application in Review first: the assistant fills the form and the
 * item waits in the review queue for the person's approval. A refusal is shown with the api's
 * sentence; the others still go.
 */
export function LikeButton({
  ids,
  onDone,
}: {
  ids: readonly string[];
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const chosen = ids.slice(0, LIKE_MAX);

  const like = async () => {
    setBusy(true);
    let liked = 0;
    const refused = new Map<string, number>();
    for (const postingId of chosen) {
      try {
        await likePosting(postingId);
        liked += 1;
        void queryClient.invalidateQueries({
          queryKey: applicationsKey(postingId),
        });
      } catch (error) {
        const why = refusalOf(error)?.detail ?? describeError(error);
        refused.set(why, (refused.get(why) ?? 0) + 1);
      }
    }
    setBusy(false);
    void queryClient.invalidateQueries({ queryKey: queueKey });
    if (liked > 0) {
      toast.success(
        `${plural(liked, "posting")} liked. The assistant fills each form, then waits for your approval in Applications.`,
        {
          duration: 10_000,
          action: {
            label: "Open Applications",
            onClick: () => {
              void navigate("/applications");
            },
          },
        },
      );
    }
    for (const [why, count] of refused) {
      toast.error(`${plural(count, "posting")} not liked: ${why}`);
    }
    onDone();
  };

  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy || chosen.length === 0}
      onClick={() => {
        void like();
      }}
    >
      {busy ? (
        <LoaderCircle aria-hidden="true" className="animate-spin" />
      ) : (
        <Heart aria-hidden="true" />
      )}
      {ids.length > LIKE_MAX ? `Like the first ${LIKE_MAX}` : "Like"}
    </Button>
  );
}
