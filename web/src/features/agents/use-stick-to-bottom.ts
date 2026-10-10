/**
 * A scrolling list that follows its newest content only while the reader is at the bottom
 * (issue #33: the answer grows many times a second, and must never pull the view away from
 * someone reading further up). Scrolling up lets go; coming back to the bottom follows again.
 *
 * "At the bottom" is judged when the content has just grown, against the height the list had
 * before it grew: the reader was at the bottom if the view ended within BOTTOM_SLACK of that
 * earlier end. No scroll listener is involved, so there is no moment where a scroll that just
 * happened is not known yet.
 */
import { useCallback, useRef, type RefObject } from "react";

/** How close to the bottom still counts as "at the bottom", in pixels. */
export const BOTTOM_SLACK = 48;

interface Box {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/**
 * Was the reader at the bottom of a list whose content was `before` pixels high? `before` null:
 * the list is new, and opens on its newest content. A list that got shorter is judged against
 * its height now (the browser already moved the view up with it).
 */
export function wasAtBottom(node: Box, before: number | null): boolean {
  if (before === null) return true;
  const end = Math.min(before, node.scrollHeight);
  return end - node.scrollTop - node.clientHeight <= BOTTOM_SLACK;
}

export function useStickToBottom<T extends HTMLElement>(
  ref: RefObject<T | null>,
) {
  const height = useRef<number | null>(null);
  /**
   * Call after the content changed: goes to the bottom if the reader was there; `force` when
   * the reader just acted (sent a message).
   */
  const follow = useCallback(
    (force = false) => {
      const node = ref.current;
      if (!node) return;
      if (force || wasAtBottom(node, height.current)) {
        node.scrollTop = node.scrollHeight;
      }
      height.current = node.scrollHeight;
    },
    [ref],
  );
  return { follow };
}
