import { ExternalLink } from "lucide-react";
import { useEffect, useId, useRef, useState, type PointerEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FitBadge } from "@/features/jobs/badges";
import { safeHttpUrl } from "@/features/jobs/text";
import { WORDING } from "@/lib/wording";
import { cn } from "@/lib/utils";

import type { Card } from "../opportunities-api";
import {
  cardLabel,
  contractLine,
  placeLine,
  reasonLine,
  sourceLine,
} from "./card-text";
import type { Swipe } from "./use-deck";

/** How far, in pixels, a drag must go for the card to leave; under it the card springs back. */
export const SWIPE_THRESHOLD = 96;
/** Where a card that leaves is sent, past the edge of a phone screen. */
const AWAY = 640;
const SPRING = "transform 320ms cubic-bezier(0.34, 1.56, 0.64, 1)";
const LEAVE = "transform 240ms cubic-bezier(0.32, 0.72, 0, 1)";

/**
 * One deck card (SPEC-opportunities.md, "The card" and "The gestures"). With motion allowed it
 * follows a mouse or a finger (pointer events, vertical scrolling left to the page): released
 * past SWIPE_THRESHOLD it leaves to that side and calls `onSwipe`, under it springs back. With
 * reduced motion it never moves: the buttons and the arrow keys do the swipes. While `disabled`
 * (a swipe under way) a drag does not start; when it ends with the card still here (a refusal)
 * the card comes back to the middle. With `focusTitle` (a card that replaced a swiped one) the
 * focus moves to its title when it appears, so a keyboard person never lands on the page's body.
 */
export function SwipeCard({
  card,
  motion,
  disabled,
  onSwipe,
  onMore,
  focusTitle = false,
}: {
  card: Card;
  motion: boolean;
  disabled: boolean;
  onSwipe: (kind: Swipe) => void;
  onMore: () => void;
  focusTitle?: boolean;
}) {
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; pointer: number } | null>(null);
  const reasonId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusTitle) titleRef.current?.focus();
  }, [focusTitle]);

  // A swipe answered with the card still here (a refusal): back to the middle.
  const [wasDisabled, setWasDisabled] = useState(disabled);
  if (wasDisabled !== disabled) {
    setWasDisabled(disabled);
    if (!disabled) setDx(0);
  }

  const down = (event: PointerEvent<HTMLElement>) => {
    if (!motion || disabled || event.button !== 0) return;
    if ((event.target as Element).closest("button, a")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { x: event.clientX, pointer: event.pointerId };
    setDragging(true);
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    if (start.current?.pointer !== event.pointerId) return;
    setDx(event.clientX - start.current.x);
  };
  const up = (event: PointerEvent<HTMLElement>) => {
    const from = start.current;
    if (from?.pointer !== event.pointerId) return;
    start.current = null;
    setDragging(false);
    const moved = event.clientX - from.x;
    if (Math.abs(moved) < SWIPE_THRESHOLD) {
      setDx(0);
      return;
    }
    setDx(moved > 0 ? AWAY : -AWAY);
    onSwipe(moved > 0 ? "like" : "pass");
  };
  const cancel = () => {
    start.current = null;
    setDragging(false);
    setDx(0);
  };

  const leaving = Math.abs(dx) >= AWAY;
  const hint = Math.min(1, Math.abs(dx) / SWIPE_THRESHOLD);
  const url = safeHttpUrl(card.url);
  const source = sourceLine(card.source);
  const reason = reasonLine(card);

  return (
    <article
      aria-label={cardLabel(card)}
      aria-describedby={reason ? reasonId : undefined}
      data-testid="deck-card"
      data-dx={dx}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      style={
        motion
          ? {
              transform: dx
                ? `translateX(${dx}px) rotate(${dx / 24}deg)`
                : undefined,
              transition: dragging ? "none" : leaving ? LEAVE : SPRING,
            }
          : undefined
      }
      className={cn(
        "relative flex touch-pan-y flex-col gap-4 rounded-2xl border bg-card p-5 text-card-foreground shadow-sm select-none",
        motion && !disabled && "cursor-grab active:cursor-grabbing",
      )}
    >
      {motion && dx !== 0 ? (
        <span
          aria-hidden="true"
          style={{ opacity: hint }}
          className={cn(
            "absolute top-4 rounded-md border-2 px-2 py-0.5 text-sm font-semibold uppercase",
            dx > 0
              ? "left-4 border-emerald-600 text-emerald-700 dark:text-emerald-400"
              : "right-4 border-rose-600 text-rose-700 dark:text-rose-400",
          )}
        >
          {dx > 0 ? WORDING.like : WORDING.pass}
        </span>
      ) : null}
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2
            ref={titleRef}
            tabIndex={-1}
            className="text-xl leading-tight font-semibold text-balance outline-none"
          >
            {card.title}
          </h2>
          <p className="text-base text-muted-foreground">{card.company}</p>
        </div>
        <FitBadge fit={card.fit} className="shrink-0" />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {placeLine(card) ? <span>{placeLine(card)}</span> : null}
        <Badge variant="secondary">{contractLine(card)}</Badge>
        {card.pay ? <span className="font-medium">{card.pay}</span> : null}
      </div>
      {reason ? (
        <p id={reasonId} className="text-sm">
          {reason}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        {source ? (
          url ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex min-h-11 items-center gap-1 text-muted-foreground underline-offset-4 hover:underline sm:min-h-0"
            >
              {source}
              <ExternalLink aria-hidden="true" className="size-3.5" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ) : (
            <span className="text-muted-foreground">{source}</span>
          )
        ) : (
          <span />
        )}
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11 sm:min-h-0"
          onClick={onMore}
        >
          More
        </Button>
      </div>
    </article>
  );
}
