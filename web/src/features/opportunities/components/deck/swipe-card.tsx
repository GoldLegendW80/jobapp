import {
  Banknote,
  BriefcaseBusiness,
  ExternalLink,
  MapPin,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type PointerEvent,
} from "react";

import { Badge } from "@/components/ui/badge";
import { PostingNotices } from "@/components/shared/jobs/posting/posting-notices";
import { Button } from "@/components/ui/button";
import { LivenessBadge } from "@/components/shared/jobs/liveness-badge";
import { safeHttpUrl } from "@/features/jobs/helpers/text";
import { WORDING } from "@/lib/wording";
import { cn } from "@/lib/utils";

import type { Card } from "@/features/opportunities/api/opportunities-api";
import type { Swipe } from "@/features/opportunities/api/deck/use-deck";
import {
  cardLabel,
  contractLine,
  placeLine,
  reasonLine,
  sourceLine,
} from "@/features/opportunities/helpers/deck/card-text";
import {
  TAP_DISTANCE,
  TAP_MS,
  initials,
  lean,
  releaseOutcome,
  releaseVelocity,
  stampOpacity,
  tilt,
  type Sample,
} from "@/features/opportunities/helpers/deck/gesture";
import {
  PUSHED,
  REST,
  RETURN,
  SETTLE,
  THROWN,
  behindPose,
  leaveDuration,
} from "@/features/opportunities/helpers/deck/motion";
import { useCardMotion } from "@/features/opportunities/hooks/deck/use-card-motion";
import type { DeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";

import { FitDial } from "./fit-dial";

/** Where a card sits in the stack: on top (it can be dragged), behind it, or leaving. */
export type CardRole = "top" | "behind" | "leaving";

/** Far enough for the card to be out of sight wherever it stands on the screen. */
function awayDistance(element: HTMLElement | null): number {
  const width = element?.offsetWidth ?? 0;
  return Math.max(640, window.innerWidth + width / 2);
}

const MAX_MATCHES = 5;

/**
 * One deck card (SPEC-opportunities.md, "The card" and "The gestures"): the posting's title,
 * company, place, contract and pay, its fit as the hero figure, what matches and what lacks, the
 * source and More. On top, with motion allowed, it follows a mouse or a finger with a tilt and
 * stamps that grow with the distance; released past the threshold (or flicked) it is a swipe,
 * under it it springs back. `fling` sends it off to that side (a swipe under way, from a drag, a
 * button or a key); a refused swipe clears it and the card comes back. `enterFrom` brings it back
 * from that side (an undo). Behind, it rises as the card on top leaves; leaving, it finishes its
 * flight and hides. With reduced motion it never moves: it fades. A tap opens More.
 */
export function SwipeCard({
  card,
  role,
  depth = 0,
  motion,
  interactive,
  fling = null,
  enterFrom = null,
  focusTitle = false,
  signals,
  onSwipe,
  onMore,
}: {
  card: Card;
  role: CardRole;
  depth?: number;
  motion: boolean;
  interactive: boolean;
  fling?: Swipe | null;
  enterFrom?: Swipe | null;
  focusTitle?: boolean;
  signals: DeckSignals;
  onSwipe: (kind: Swipe) => void;
  onMore: () => void;
}) {
  const element = useRef<HTMLElement>(null);
  const likeStamp = useRef<HTMLDivElement>(null);
  const passStamp = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const reasonId = useId();

  const motionApi = useCardMotion(element, (pose) => {
    const like = pose.x > 0 ? stampOpacity(pose.x) : 0;
    const pass = pose.x < 0 ? stampOpacity(pose.x) : 0;
    for (const [stamp, value] of [
      [likeStamp.current, like],
      [passStamp.current, pass],
    ] as const) {
      if (!stamp) continue;
      stamp.style.opacity = String(value);
      stamp.style.transform = `scale(${(0.86 + 0.14 * value).toFixed(3)})`;
    }
    if (role === "top") signals.setLean(lean(pose.x));
  });

  useEffect(() => {
    if (focusTitle && role === "top") titleRef.current?.focus();
  }, [focusTitle, role]);

  // Only the card on top is reachable: React 18 drops a boolean `inert`, so it is set here.
  useLayoutEffect(() => {
    element.current?.toggleAttribute("inert", role !== "top");
  }, [role]);

  // What the card is doing now; each change starts the matching move from where it is.
  const away = role === "leaving" || (role === "top" && fling !== null);
  const kind = fling ?? "pass";
  const intent = away
    ? `away:${kind}`
    : role === "behind"
      ? `behind:${depth}`
      : "rest";
  const throwVelocity = useRef<{ vx: number; vy: number } | null>(null);
  const mounted = useRef(false);
  // Off the screen after its flight; a card that left then hides until the stack lets it go.
  const parked = useRef(false);
  const leaving = useRef(role === "leaving");
  const hide = useCallback(() => {
    if (element.current) element.current.style.visibility = "hidden";
  }, []);
  useLayoutEffect(() => {
    leaving.current = role === "leaving";
    if (role === "leaving" && parked.current) hide();
  }, [role, hide]);

  useLayoutEffect(() => {
    const first = !mounted.current;
    mounted.current = true;
    const target = element.current;
    const sign = kind === "like" ? 1 : -1;
    if (target)
      target.dataset.dx = String(away ? sign * awayDistance(target) : 0);
    const arrived = () => {
      parked.current = true;
      if (leaving.current) hide();
    };
    if (!away) {
      parked.current = false;
      if (target) target.style.visibility = "";
    }

    if (!motion) {
      if (away) motionApi.tween({ opacity: 0 }, 150, (t) => t, arrived);
      else if (first && enterFrom) {
        motionApi.set({ opacity: 0 });
        motionApi.tween({ opacity: 1 }, 150, (t) => t);
      } else motionApi.tween({ opacity: 1 }, 150, (t) => t);
      return;
    }

    if (away) {
      const now = motionApi.pose();
      const distance = awayDistance(target);
      const thrown = throwVelocity.current;
      throwVelocity.current = null;
      const ms = leaveDuration(
        Math.abs(sign * distance - now.x),
        thrown ? Math.abs(thrown.vx) : 0,
      );
      motionApi.tween(
        {
          x: sign * distance,
          y: now.y + (thrown ? thrown.vy * ms * 0.5 : 36),
          rotate: sign * 26,
          scale: 1,
          opacity: 1,
        },
        ms,
        thrown ? THROWN : PUSHED,
        arrived,
      );
      return;
    }

    if (role === "behind") {
      const pose = behindPose(depth, Math.abs(signals.lean()));
      if (first) {
        motionApi.set({ ...pose, y: pose.y + 48, opacity: 0 });
        motionApi.spring(pose, SETTLE);
      } else motionApi.spring(pose, SETTLE);
      return;
    }

    // On top, at rest: the deck leans nowhere yet.
    signals.setLean(0);
    if (first && enterFrom) {
      const from = enterFrom === "like" ? 1 : -1;
      motionApi.set({
        x: from * awayDistance(target),
        y: 24,
        rotate: from * 26,
        scale: 1,
        opacity: 1,
      });
      motionApi.spring(REST, RETURN);
    } else if (first) {
      motionApi.set({ ...REST, y: 40, scale: 0.94, opacity: 0 });
      motionApi.spring(REST, SETTLE);
    } else motionApi.spring(REST, motionApi.pose().x === 0 ? SETTLE : RETURN);
    // The intent alone starts a move; the rest is read when it changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent, motion]);

  // Behind, the card rises with the lean of the one on top.
  useEffect(() => {
    if (role !== "behind" || !motion) return;
    return signals.onLean((value) => {
      motionApi.set(behindPose(depth, Math.abs(value)));
    });
  }, [role, depth, motion, signals, motionApi]);

  const drag = useRef<{
    pointer: number;
    startX: number;
    startY: number;
    fromX: number;
    fromY: number;
    low: boolean;
    samples: Sample[];
    at: number;
  } | null>(null);

  const draggable = role === "top" && interactive && fling === null;

  const down = (event: PointerEvent<HTMLElement>) => {
    if (!draggable || event.button !== 0) return;
    if ((event.target as Element).closest("button, a")) return;
    const box = event.currentTarget.getBoundingClientRect();
    const from = motionApi.pose();
    motionApi.stop();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointer: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      fromX: from.x,
      fromY: from.y,
      low: box.height > 0 && event.clientY - box.top > box.height * 0.6,
      samples: [{ x: event.clientX, y: event.clientY, t: event.timeStamp }],
      at: event.timeStamp,
    };
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    const now = drag.current;
    if (now?.pointer !== event.pointerId) return;
    now.samples.push({
      x: event.clientX,
      y: event.clientY,
      t: event.timeStamp,
    });
    if (now.samples.length > 12) now.samples.shift();
    if (!motion) return;
    const dx = now.fromX + event.clientX - now.startX;
    const dy = now.fromY + (event.clientY - now.startY) * 0.35;
    motionApi.set({
      x: dx,
      y: dy,
      rotate: tilt(dx, now.low),
      scale: 1.02,
      opacity: 1,
    });
    if (element.current) element.current.dataset.dx = String(Math.round(dx));
  };
  const up = (event: PointerEvent<HTMLElement>) => {
    const now = drag.current;
    if (now?.pointer !== event.pointerId) return;
    drag.current = null;
    now.samples.push({
      x: event.clientX,
      y: event.clientY,
      t: event.timeStamp,
    });
    const moved = Math.hypot(
      event.clientX - now.startX,
      event.clientY - now.startY,
    );
    if (moved < TAP_DISTANCE && event.timeStamp - now.at < TAP_MS) {
      if (motion) motionApi.spring(REST, SETTLE);
      onMore();
      return;
    }
    if (!motion) return;
    const dx = now.fromX + event.clientX - now.startX;
    const velocity = releaseVelocity(now.samples);
    const outcome = releaseOutcome(dx, velocity.vx);
    if (outcome) {
      throwVelocity.current = velocity;
      if (element.current)
        element.current.dataset.dx = String(
          (outcome === "like" ? 1 : -1) * awayDistance(element.current),
        );
      onSwipe(outcome);
      return;
    }
    if (element.current) element.current.dataset.dx = "0";
    motionApi.spring(REST, SETTLE, {
      x: velocity.vx * 1000,
      y: velocity.vy * 1000,
    });
  };
  const cancel = (event: PointerEvent<HTMLElement>) => {
    if (drag.current?.pointer !== event.pointerId) return;
    drag.current = null;
    if (element.current) element.current.dataset.dx = "0";
    if (motion) motionApi.spring(REST, SETTLE);
  };

  const url = safeHttpUrl(card.url);
  const source = sourceLine(card.source);
  const reason = reasonLine(card);
  const place = placeLine(card);
  const { matches, gap, text } = card.reason;
  const chips = matches.length > 0 || gap !== null;
  const front = role === "top";

  return (
    <article
      ref={element}
      aria-label={front ? cardLabel(card) : undefined}
      aria-describedby={front && reason ? reasonId : undefined}
      aria-hidden={front ? undefined : true}
      data-testid={
        front
          ? "deck-card"
          : role === "leaving"
            ? "deck-card-leaving"
            : "deck-card-behind"
      }
      data-dx="0"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      onLostPointerCapture={cancel}
      style={{
        zIndex: role === "leaving" ? 30 : 20 - depth,
        // Behind, a card shrinks toward its foot so its edge shows under the top one.
        transformOrigin: role === "behind" ? "50% 100%" : "50% 50%",
      }}
      className={cn(
        "absolute inset-x-0 top-0 bottom-8 flex flex-col overflow-hidden rounded-3xl bg-card text-card-foreground shadow-lg ring-1 ring-foreground/5 select-none will-change-transform",
        front && motion && "touch-none",
        draggable && motion && "cursor-grab active:cursor-grabbing",
      )}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-44 bg-[linear-gradient(180deg,color-mix(in_oklch,var(--brand)_16%,var(--card))_0%,color-mix(in_oklch,var(--highlight)_40%,var(--card))_50%,var(--card)_100%)]"
      />
      <div className="relative flex min-h-0 flex-1 flex-col gap-4 p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <span
            aria-hidden="true"
            className="grid size-14 shrink-0 place-items-center rounded-2xl bg-[linear-gradient(140deg,color-mix(in_oklch,var(--brand)_70%,white),var(--brand))] text-lg font-semibold text-brand-foreground shadow-md"
          >
            {initials(card.company)}
          </span>
          <FitDial fit={card.fit} />
        </div>
        <div className="flex flex-col gap-1">
          <h2
            ref={titleRef}
            tabIndex={-1}
            className="line-clamp-3 text-2xl leading-tight font-semibold tracking-tight text-balance outline-none"
          >
            {card.title}
          </h2>
          <p className="line-clamp-2 text-base text-muted-foreground">
            {card.company}
          </p>
          <LivenessBadge liveness={card.liveness} className="mt-1" />
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
          {place ? (
            <li className="flex min-w-0 items-center gap-1.5">
              <MapPin
                aria-hidden="true"
                className="size-4 shrink-0 text-muted-foreground"
              />
              <span className="line-clamp-2">{place}</span>
            </li>
          ) : null}
          <li className="flex items-center gap-1.5">
            <BriefcaseBusiness
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground"
            />
            <span>{contractLine(card)}</span>
          </li>
          <li className="flex min-w-0 items-center gap-1.5">
            <Banknote
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground"
            />
            {card.pay ? (
              <span data-pay="stated" className="font-medium tabular-nums">
                {card.pay}
              </span>
            ) : (
              <span data-pay="none" className="text-muted-foreground">
                {WORDING.payNotGiven}
              </span>
            )}
          </li>
        </ul>
        <PostingNotices notices={card.notices} compact max={2} />
        {chips ? (
          <div
            aria-hidden="true"
            className="flex min-h-0 flex-col gap-3 overflow-hidden"
          >
            {matches.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                <h3 className="text-sm font-medium">{WORDING.matches}</h3>
                <div className="flex flex-wrap gap-1.5">
                  {matches.slice(0, MAX_MATCHES).map((match) => (
                    <Badge
                      key={match}
                      variant="secondary"
                      className="bg-highlight text-sm font-normal text-highlight-foreground"
                    >
                      {match}
                    </Badge>
                  ))}
                  {matches.length > MAX_MATCHES ? (
                    <Badge variant="outline" className="text-sm font-normal">
                      {`${matches.length - MAX_MATCHES} more`}
                    </Badge>
                  ) : null}
                </div>
              </div>
            ) : null}
            {gap ? (
              <div className="flex flex-col gap-1.5">
                <h3 className="text-sm font-medium">{WORDING.gap}</h3>
                <Badge variant="warning" className="text-sm font-normal">
                  {gap}
                </Badge>
              </div>
            ) : null}
          </div>
        ) : null}
        {reason ? (
          <p
            id={reasonId}
            className={cn(
              chips ? "sr-only" : "line-clamp-4 text-sm leading-relaxed",
            )}
          >
            {chips ? reason : text}
          </p>
        ) : null}
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1 text-sm">
          {source ? (
            url ? (
              <a
                href={url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex min-h-11 items-center gap-1 rounded-md text-muted-foreground underline-offset-4 hover:text-foreground hover:underline sm:min-h-9"
              >
                {source}
                <ExternalLink aria-hidden="true" className="size-3.5" />
                <span className="sr-only">{WORDING.opensNewTab}</span>
              </a>
            ) : (
              <span className="text-muted-foreground">{source}</span>
            )
          ) : (
            <span />
          )}
          <Button
            variant="outline"
            size="sm"
            className="min-h-11 rounded-full px-4 sm:min-h-9"
            onClick={onMore}
          >
            {WORDING.more}
          </Button>
        </div>
      </div>
      <div
        ref={likeStamp}
        aria-hidden="true"
        data-stamp="like"
        style={{ opacity: 0 }}
        className="pointer-events-none absolute top-7 left-6 -rotate-12 rounded-xl border-[3px] border-brand bg-card/80 px-3 py-0.5 text-3xl font-bold text-brand backdrop-blur-sm"
      >
        {WORDING.like}
      </div>
      <div
        ref={passStamp}
        aria-hidden="true"
        data-stamp="pass"
        style={{ opacity: 0 }}
        className="pointer-events-none absolute top-7 right-6 rotate-12 rounded-xl border-[3px] border-destructive bg-card/80 px-3 py-0.5 text-3xl font-bold text-destructive backdrop-blur-sm"
      >
        {WORDING.pass}
      </div>
    </article>
  );
}
