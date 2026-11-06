import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The profile changed since this part was written: Done would undo that, so rewrite it first. */
export function ProfileChangedAlert({
  busy,
  onRewrite,
}: {
  busy: boolean;
  onRewrite: () => void;
}) {
  return (
    <Alert>
      <AlertTitle>{W.elsewhereTitle}</AlertTitle>
      <AlertDescription>{W.elsewhere}</AlertDescription>
      <Button
        size="sm"
        variant="outline"
        className="mt-2 w-fit"
        disabled={busy}
        onClick={onRewrite}
      >
        {W.rewrite}
      </Button>
    </Alert>
  );
}
