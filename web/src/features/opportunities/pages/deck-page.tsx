import { useEffect, useMemo, useRef, useState } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { NO_FILTERS, type DeckFilters } from "../api/opportunities-api";
import { useDeck, type Swipe } from "../api/deck/use-deck";
import { MODE_SENTENCES, SWIPE_TEXT } from "../helpers/deck/card-text";
import { DeckBackdrop } from "../components/deck/deck-backdrop";
import { DeckControls } from "../components/deck/deck-controls";
import { DeckEmpty } from "../components/deck/deck-empty";
import { DeckRail } from "../components/deck/deck-rail";
import { DeckScene } from "../components/deck/deck-scene";
import { DeckStack, type Toward } from "../components/deck/deck-stack";
import { DeckTopBar } from "../components/deck/deck-top-bar";
import { PassReason } from "../components/deck/pass-reason";
import { PostingSheet } from "../components/deck/posting-sheet";
import { SwipeHint } from "../components/deck/swipe-hint";
import { useDeckSignals } from "../hooks/deck/use-deck-signals";
import { useFinePointer } from "../hooks/deck/use-fine-pointer";
import { useReducedMotion } from "../hooks/deck/use-reduced-motion";

/** Keys a deck listens to, unless the focus is in a field or a dialog is open. */
function ignoredTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "input, textarea, select, [role='checkbox'], [contenteditable='true'], [role='dialog'], [role='alertdialog']",
    ),
  );
}

/** Set once the person has swiped: the first visit's hint does not come back. */
const HINT_KEY = "discover:swipe-hint";
/** How long the hint stays on a visit when the person does not swipe. */
const HINT_MS = 9000;

function hintSeen(): boolean {
  try {
    return window.localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return true;
  }
}

/**
 * /discover, the swipe deck (SPEC-opportunities.md, "The swipe deck"), the product's bright
 * moment: edge to edge under the header, one posting at a time on a stack of cards, best fit
 * first. Like and Pass by a drag (the card follows the finger, tilts, shows its stamp, flies off
 * past the threshold or springs back), by the buttons or by the arrow keys (left Pass, right
 * Like, U undoes the last swipe); Undo last brings the card back the way it left. The mode a Like
 * runs in stays in sight above the deck. A Rive heart bursts on an accepted Like (an X on a
 * Pass), a hint shows the gesture on the first visit, and the empty deck celebrates; all of it
 * loads after the deck works, and is left out with reduced motion. The side rail (under the deck
 * on a phone) says what a Like does, the keys and the filters under Advanced. Without a card,
 * Pass and Like are gone and Undo last stays only while there is a swipe to undo.
 */
export function DeckPage() {
  useTitle(WORDING.discover);
  const [filters, setFilters] = useState<DeckFilters>(NO_FILTERS);
  const deck = useDeck(filters);
  const motion = !useReducedMotion();
  // The keys are offered where a keyboard is likely: a mouse or a trackpad, not a touch screen.
  const finePointer = useFinePointer();
  const signals = useDeckSignals();
  const [moreOpen, setMoreOpen] = useState(false);
  const { top, busy, refusal, last } = deck;
  const likeRefused = refusal?.kind === "like";
  // Once the person swiped or undid, a new card on top takes the focus and is announced.
  const [moved, setMoved] = useState(false);
  // The card on its way off (a swipe under way, kept after it left so it finishes its flight),
  // and the card an undo brings back, from the side it left by.
  const [fling, setFling] = useState<Toward | null>(null);
  const [returning, setReturning] = useState<Toward | null>(null);
  const [hint, setHint] = useState(() => !hintSeen());

  const swipe = (kind: Swipe) => {
    setMoved(true);
    if (top && !busy && !(kind === "like" && likeRefused)) {
      const card = top;
      setFling((now) => (now?.id === card.id ? now : { id: card.id, kind }));
      setHint(false);
      try {
        window.localStorage.setItem(HINT_KEY, "1");
      } catch {
        // Storage blocked: the hint only shows again on the next visit.
      }
    }
    deck.swipe(kind);
  };
  const undo = () => {
    if (last && !busy) {
      setFling(null);
      setReturning({ id: last.card.id, kind: last.kind });
    }
    setMoved(true);
    deck.undo();
  };
  const changeFilters = (next: DeckFilters) => {
    setMoved(false);
    setFling(null);
    setReturning(null);
    setFilters(next);
  };

  // A swipe answered with the card still on top (refused, or the network failed): it comes back.
  const [answered, setAnswered] = useState({ busy, top: top?.id });
  if (answered.busy !== busy || answered.top !== top?.id) {
    setAnswered({ busy, top: top?.id });
    if (fling && !busy && top?.id === fling.id) setFling(null);
  }

  // An accepted Like or Pass bursts over the deck.
  useEffect(() => {
    if (last) signals.burst(last.kind);
  }, [last, signals]);

  useEffect(() => {
    if (!hint) return;
    const id = window.setTimeout(() => {
      setHint(false);
    }, HINT_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [hint]);

  // The boards of the cards read ("greenhouse" of "greenhouse:acme", the api matches its every
  // company), plus the one chosen, so a chosen source stays in the list.
  const pages = deck.query.data?.pages;
  const sources = useMemo(() => {
    const named = (pages ?? []).flatMap((p) =>
      p.cards.map((c) => (c.source ?? "").split(":")[0] ?? ""),
    );
    if (filters.source) named.push(filters.source);
    return [...new Set(named.filter(Boolean))].sort();
  }, [pages, filters.source]);

  const keys = useRef({ swipe, undo, open: moreOpen });
  useEffect(() => {
    keys.current = { swipe, undo, open: moreOpen };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey
      )
        return;
      if (keys.current.open || ignoredTarget(event.target)) return;
      if (event.key === "ArrowLeft") keys.current.swipe("pass");
      else if (event.key === "ArrowRight") keys.current.swipe("like");
      else if (event.key === "u" || event.key === "U") keys.current.undo();
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const filtered =
    filters.types.length > 0 ||
    filters.remote !== null ||
    filters.source !== null;
  const done = last
    ? `${last.kind === "like" ? "Liked" : "Passed"} ${last.card.title}. `
    : "";
  const announcement = !moved
    ? ""
    : top
      ? `${done}Now showing ${top.title} at ${top.company}.`
      : `${done}No more postings to show.`;

  return (
    // The shell pads its main area (app-shell.tsx); the stage cancels it to reach the edges.
    <div className="-mx-4 -my-6 sm:-mx-6 sm:-my-10 lg:-mx-8">
      <div className="relative isolate overflow-hidden">
        <DeckBackdrop signals={signals} motion={motion} />
        <div className="mx-auto grid w-full max-w-360 gap-x-10 px-4 sm:px-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:px-8">
          <div className="flex min-h-[calc(100dvh-4rem-var(--bottom-bar,0px))] min-w-0 flex-col gap-4 py-4 sm:gap-6 sm:py-6 lg:py-8">
            <DeckTopBar mode={deck.mode} left={top ? deck.left : undefined} />
            <p
              className="sr-only"
              aria-live="polite"
              data-testid="deck-announce"
            >
              {announcement}
            </p>
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center">
              {deck.query.isPending ? (
                <div
                  role="status"
                  className="relative flex h-[min(34rem,62dvh)] w-full max-w-[26rem] items-center justify-center"
                >
                  <span className="sr-only">Loading your deck</span>
                  <Skeleton
                    aria-hidden="true"
                    className="absolute inset-x-0 top-0 bottom-8 rounded-3xl"
                  />
                  <DeckScene
                    scene="Loading"
                    motion={motion}
                    className="h-48 w-60"
                  />
                </div>
              ) : deck.query.isError && !top ? (
                <div className="w-full max-w-md">
                  <LoadError query={deck.query}>
                    Your deck could not be loaded.
                  </LoadError>
                </div>
              ) : top ? (
                <section
                  aria-label={WORDING.deck}
                  className="flex w-full max-w-[26rem] flex-1 flex-col gap-3"
                >
                  <DeckStack
                    cards={deck.cards}
                    motion={motion}
                    busy={busy}
                    fling={fling}
                    returning={returning}
                    focusTitle={moved}
                    signals={signals}
                    onSwipe={swipe}
                    onMore={() => {
                      setMoreOpen(true);
                    }}
                    className="max-h-[40rem] min-h-[26rem] w-full flex-1 sm:min-h-[32rem]"
                  >
                    <DeckScene
                      scene="Reaction"
                      motion={motion}
                      signals={signals}
                      className="absolute inset-x-0 top-0 bottom-8 z-40"
                    />
                    {hint && motion && !busy ? (
                      <SwipeHint
                        motion={motion}
                        keys={finePointer}
                        className="absolute inset-x-0 bottom-24 z-40 mx-auto w-fit"
                      />
                    ) : null}
                  </DeckStack>
                  {refusal ? (
                    <Alert role="alert" className="bg-card/90 backdrop-blur-sm">
                      <AlertDescription>
                        {refusal.detail}
                        {likeRefused ? ` ${SWIPE_TEXT.passOffered}` : ""}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  <PostingSheet
                    card={top}
                    open={moreOpen}
                    onOpenChange={setMoreOpen}
                  />
                </section>
              ) : deck.rulesUnreadable ? (
                <Alert role="status" className="max-w-md">
                  <AlertDescription>
                    Your roles to never apply for could not be read, so the deck
                    shows nothing until they are fixed in Search options.
                  </AlertDescription>
                </Alert>
              ) : deck.query.isFetchingNextPage ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Spinner aria-hidden="true" role={undefined} />
                  Loading more postings
                </p>
              ) : (
                <div className="w-full max-w-lg">
                  <DeckEmpty
                    threshold={deck.minFit ?? 70}
                    filtered={filtered}
                    motion={motion}
                  />
                </div>
              )}
            </div>
            {top || last ? (
              <DeckControls
                hasCard={Boolean(top)}
                canUndo={Boolean(last)}
                busy={busy}
                likeRefused={likeRefused}
                onPass={() => {
                  swipe("pass");
                }}
                onLike={() => {
                  swipe("like");
                }}
                onUndo={undo}
              />
            ) : null}
            {last?.kind === "pass" ? (
              <PassReason key={last.card.id} postingId={last.card.id} />
            ) : null}
            {top && deck.mode === "full_auto" ? (
              // Beside the deck the rail says it; stacked, the words stay under Like.
              <p className="mx-auto max-w-sm text-center text-sm text-muted-foreground lg:hidden">
                {MODE_SENTENCES.full_auto}
              </p>
            ) : null}
          </div>
          <aside className="flex min-w-0 flex-col gap-6 pb-10 lg:py-8">
            <DeckRail
              mode={deck.mode}
              keys={Boolean(top) && finePointer}
              filters={filters}
              sources={sources}
              onFiltersChange={changeFilters}
            />
          </aside>
        </div>
      </div>
    </div>
  );
}
