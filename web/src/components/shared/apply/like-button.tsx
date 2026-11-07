import { Heart } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { plural } from "@/features/jobs/helpers/selection";

import { useLikePostings } from "@/features/apply/api/use-like-postings";

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
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const likePostings = useLikePostings();

  const like = async () => {
    setBusy(true);
    const { liked, fullAuto, rehearsals, refused } = await likePostings(ids);
    setBusy(false);
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
        <Spinner aria-hidden="true" role={undefined} />
      ) : (
        <Heart aria-hidden="true" />
      )}
      Like
    </Button>
  );
}
