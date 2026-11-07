import { useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import type { Card } from "@/features/opportunities/api/opportunities-api";
import type { Swipe } from "@/features/opportunities/api/deck/use-deck";
import type { DeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";

import { SwipeCard } from "./swipe-card";

/** A swipe of the card `id` toward `kind`. */
export interface Toward {
  id: string;
  kind: Swipe;
}

/** The cards drawn behind the one on top. */
const BEHIND = 2;
/** The cards that left and may still be in flight. */
const GHOSTS = 2;

/**
 * The deck as a stack of cards: the one on top (dragged, or sent off by `fling`), two behind it
 * that rise as it leaves, and the cards that just left, finishing their flight. A card is keyed by
 * its posting, so the one that leaves keeps its motion and the one behind becomes the top without
 * a jump. `returning` brings a card back from the side it left by (an undo). With reduced motion
 * only the top card is drawn, and cards fade instead of flying.
 */
export function DeckStack({
  cards,
  motion,
  busy,
  fling,
  returning,
  focusTitle,
  signals,
  onSwipe,
  onMore,
  className,
  children,
}: {
  cards: readonly Card[];
  motion: boolean;
  busy: boolean;
  fling: Toward | null;
  returning: Toward | null;
  focusTitle: boolean;
  signals: DeckSignals;
  onSwipe: (kind: Swipe) => void;
  onMore: () => void;
  className?: string;
  /** Drawn over the cards: the reaction, the first visit's hint. */
  children?: ReactNode;
}) {
  const top = cards[0];
  const [ghosts, setGhosts] = useState<readonly Toward[]>([]);
  const [known, setKnown] = useState<ReadonlyMap<string, Card>>(new Map());
  const [seenTop, setSeenTop] = useState(top);
  if (seenTop?.id !== top?.id) {
    setSeenTop(top);
    const left = seenTop;
    if (left && fling?.id === left.id && !cards.some((c) => c.id === left.id)) {
      setGhosts((now) =>
        [
          ...now.filter((g) => g.id !== left.id),
          { id: left.id, kind: fling.kind },
        ].slice(-GHOSTS),
      );
      setKnown((now) => new Map(now).set(left.id, left));
    }
  }

  const shown = cards.slice(0, motion ? 1 + BEHIND : 1);
  const showing = new Set(shown.map((card) => card.id));
  const flying = ghosts.flatMap((ghost) => {
    const card = known.get(ghost.id);
    return card && !showing.has(ghost.id) ? [{ card, kind: ghost.kind }] : [];
  });

  return (
    <div className={cn("relative", className)}>
      {/* One list, so a card keeps its motion from the top to its flight off the screen. */}
      {[
        ...flying.map(({ card, kind }) => (
          <SwipeCard
            key={card.id}
            card={card}
            role="leaving"
            motion={motion}
            interactive={false}
            fling={kind}
            signals={signals}
            onSwipe={onSwipe}
            onMore={onMore}
          />
        )),
        ...shown.map((card, depth) => (
          <SwipeCard
            key={card.id}
            card={card}
            role={depth === 0 ? "top" : "behind"}
            depth={depth}
            motion={motion}
            interactive={depth === 0 && !busy}
            fling={depth === 0 && fling?.id === card.id ? fling.kind : null}
            enterFrom={returning?.id === card.id ? returning.kind : null}
            focusTitle={depth === 0 && focusTitle}
            signals={signals}
            onSwipe={onSwipe}
            onMore={onMore}
          />
        )),
      ]}
      {children}
    </div>
  );
}
