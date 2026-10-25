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

/**
 * The Like of the jobs list's bulk bar, until the swipe deck (#74) sends Likes: one Like per
 * selected posting, one after the other. A Like is the application in Review first: the assistant
 * fills the form and the item waits in the review queue for the person's approval. A refusal is
 * shown with the api's sentence; the others still go.
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

  const like = async () => {
    setBusy(true);
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
    setBusy(false);
    void queryClient.invalidateQueries({ queryKey: queueKey });
    if (liked > 0) {
      toast.success(
        rehearsals > 0
          ? `${plural(liked, "posting")} liked. Full auto rehearses first: the app fills the form, submits nothing, and asks you "Does this look right?" in Applications.`
          : fullAuto > 0
            ? `${plural(liked, "posting")} liked. In Full auto, your Like is your approval: the app fills each form and submits it after the hold when every check passes; the rest waits for you in Applications.`
            : `${plural(liked, "posting")} liked. The assistant fills each form, then waits for your approval in Applications.`,
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
      disabled={busy || ids.length === 0}
      onClick={() => {
        void like();
      }}
    >
      {busy ? (
        <LoaderCircle aria-hidden="true" className="animate-spin" />
      ) : (
        <Heart aria-hidden="true" />
      )}
      Like
    </Button>
  );
}
