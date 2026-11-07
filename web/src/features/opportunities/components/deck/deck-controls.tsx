import { Heart, Undo2, X } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { WORDING } from "@/lib/wording";
import { cn } from "@/lib/utils";

const ROUND =
  "rounded-full transition-[transform,box-shadow,background-color] duration-150 ease-out active:scale-90 motion-reduce:transition-none motion-reduce:active:scale-100";
const NAME = "cursor-pointer text-sm leading-5 text-muted-foreground";

/**
 * Pass, Undo last and Like under the deck, within a thumb's reach, each named under it (a tap on
 * the name acts too). Pass and Like show only with a card to swipe; Undo last stays while there
 * is a swipe to undo. While a swipe is under way all three wait; a refused Like keeps Like off.
 */
export function DeckControls({
  hasCard,
  canUndo,
  busy,
  likeRefused,
  onPass,
  onLike,
  onUndo,
}: {
  hasCard: boolean;
  canUndo: boolean;
  busy: boolean;
  likeRefused: boolean;
  onPass: () => void;
  onLike: () => void;
  onUndo: () => void;
}) {
  const passId = useId();
  const likeId = useId();
  const undoId = useId();
  return (
    <div
      data-testid="deck-controls"
      className="flex items-start justify-center gap-6 sm:gap-8"
    >
      {hasCard ? (
        <div className="flex flex-col items-center gap-1.5">
          <Button
            id={passId}
            variant="outline"
            className={cn(
              ROUND,
              "size-16 border-0 bg-card text-destructive shadow-md ring-1 ring-foreground/5 hover:bg-card hover:text-destructive hover:shadow-lg sm:size-[4.5rem]",
            )}
            aria-label={WORDING.pass}
            aria-keyshortcuts="ArrowLeft"
            disabled={busy}
            onClick={onPass}
          >
            <X aria-hidden="true" className="size-8" strokeWidth={2.5} />
          </Button>
          <Label htmlFor={passId} className={NAME}>
            {WORDING.pass}
          </Label>
        </div>
      ) : null}
      <div className="flex flex-col items-center gap-1.5 pt-2.5 sm:pt-3.5">
        <Button
          id={undoId}
          variant="outline"
          className={cn(
            ROUND,
            "size-11 border-0 bg-card shadow-sm ring-1 ring-foreground/5 hover:bg-card",
          )}
          aria-label={WORDING.undoLast}
          aria-keyshortcuts="U"
          disabled={!canUndo || busy}
          onClick={onUndo}
        >
          <Undo2 aria-hidden="true" className="size-5" />
        </Button>
        <Label htmlFor={undoId} className={cn(NAME, "text-xs")}>
          {WORDING.undoLast}
        </Label>
      </div>
      {hasCard ? (
        <div className="flex flex-col items-center gap-1.5">
          <Button
            id={likeId}
            className={cn(
              ROUND,
              "size-16 bg-brand text-brand-foreground shadow-lg shadow-brand/30 hover:bg-brand/90 hover:shadow-xl hover:shadow-brand/40 sm:size-[4.5rem]",
            )}
            aria-label={WORDING.like}
            aria-keyshortcuts="ArrowRight"
            disabled={busy || likeRefused}
            onClick={onLike}
          >
            {busy ? (
              <Spinner aria-hidden="true" role={undefined} className="size-7" />
            ) : (
              <Heart aria-hidden="true" className="size-8 fill-current" />
            )}
          </Button>
          <Label htmlFor={likeId} className={NAME}>
            {WORDING.like}
          </Label>
        </div>
      ) : null}
    </div>
  );
}
