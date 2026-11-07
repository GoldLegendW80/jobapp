import { MoveHorizontal } from "lucide-react";

import { cn } from "@/lib/utils";

import { DeckScene } from "./deck-scene";

/**
 * The first visit's hint, over the lower part of the card: a finger drags a small card right
 * (Like) then left (Pass), with the words. It lets every tap and drag through to the card, and
 * the page takes it away at the first swipe. Decoration for the eyes: the keys and the buttons
 * are named elsewhere for a screen reader.
 */
export function SwipeHint({
  motion,
  keys,
  className,
}: {
  motion: boolean;
  /** Whether to name the arrow keys too (a precise pointer, so a keyboard is likely). */
  keys: boolean;
  className?: string;
}) {
  return (
    <div
      aria-hidden="true"
      data-testid="deck-hint"
      className={cn(
        "pointer-events-none flex items-center gap-3 rounded-2xl bg-foreground/90 py-2 pr-4 pl-2 text-sm text-background shadow-lg backdrop-blur-sm animate-in fade-in slide-in-from-bottom-2 duration-500 motion-reduce:animate-none",
        className,
      )}
    >
      <DeckScene
        scene="Hint"
        motion={motion}
        className="h-16 w-20 shrink-0"
        fallback={<MoveHorizontal className="size-6" />}
      />
      <p className="max-w-56 leading-snug">
        {keys
          ? "Drag the card right to like it, left to pass, or use the arrow keys."
          : "Drag the card right to like it, left to pass."}
      </p>
    </div>
  );
}
