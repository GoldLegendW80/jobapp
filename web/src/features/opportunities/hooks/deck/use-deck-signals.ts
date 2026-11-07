import { useState } from "react";

import type { Swipe } from "@/features/opportunities/api/deck/use-deck";

/**
 * What the card on top tells the rest of the deck frame by frame, without a React render: its
 * lean while dragged or thrown (-1 Pass to 1 Like, read by the cards behind, the backdrop and the
 * Rive reaction), and the bursts of an accepted Like or Pass.
 */
export interface DeckSignals {
  lean: () => number;
  setLean: (value: number) => void;
  onLean: (listener: (value: number) => void) => () => void;
  burst: (kind: Swipe) => void;
  onBurst: (listener: (kind: Swipe) => void) => () => void;
}

export function createDeckSignals(): DeckSignals {
  let current = 0;
  const leans = new Set<(value: number) => void>();
  const bursts = new Set<(kind: Swipe) => void>();
  return {
    lean: () => current,
    setLean: (value) => {
      if (value === current) return;
      current = value;
      for (const listener of leans) listener(value);
    },
    onLean: (listener) => {
      leans.add(listener);
      return () => {
        leans.delete(listener);
      };
    },
    burst: (kind) => {
      for (const listener of bursts) listener(kind);
    },
    onBurst: (listener) => {
      bursts.add(listener);
      return () => {
        bursts.delete(listener);
      };
    },
  };
}

/** One set of signals for the life of the page. */
export function useDeckSignals(): DeckSignals {
  const [signals] = useState(createDeckSignals);
  return signals;
}
