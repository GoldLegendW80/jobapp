import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The document was saved in another tab: load that one, or save this one over it. */
export function SaveConflictAlert({
  onSettle,
}: {
  onSettle: (keep: "theirs" | "mine") => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{W.conflictTitle}</AlertTitle>
      <AlertDescription>{W.conflict}</AlertDescription>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => {
            onSettle("theirs");
          }}
        >
          {W.loadLatest}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            onSettle("mine");
          }}
        >
          {W.keepMine}
        </Button>
      </div>
    </Alert>
  );
}
