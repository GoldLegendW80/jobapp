import { Heart, LoaderCircle, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";
import { WORDING, deckEmpty } from "@/lib/wording";

import { NO_FILTERS, type DeckFilters } from "../opportunities-api";
import { MODE_WORDS, SWIPE_TEXT } from "./card-text";
import { DeckFilterRow } from "./filter-row";
import { PostingSheet } from "./posting-sheet";
import { SwipeCard } from "./swipe-card";
import { useDeck, type Swipe } from "./use-deck";
import { useReducedMotion } from "./use-reduced-motion";

/** Keys a deck listens to, unless the focus is in a field or a dialog is open. */
function ignoredTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "input, textarea, select, [contenteditable='true'], [role='dialog'], [role='alertdialog']",
    ),
  );
}

/**
 * /discover, the swipe deck (SPEC-opportunities.md, "The swipe deck"): one card at a time, Like
 * and Pass by drag, by the buttons or by the arrow keys (left Pass, right Like, U undoes the
 * last swipe), Undo last until the next swipe, the mode a Like runs in, the filter
 * row under Advanced and the empty state naming the person's threshold. Phone width first: the
 * buttons sit under the card, within a thumb's reach.
 */
export function DeckPage() {
  useTitle(WORDING.discover);
  const [filters, setFilters] = useState<DeckFilters>(NO_FILTERS);
  const deck = useDeck(filters);
  const motion = !useReducedMotion();
  const [moreOpen, setMoreOpen] = useState(false);
  const { top, busy, refusal, last } = deck;
  // Once the person swiped or undid, a new card on top takes the focus and is announced.
  const [moved, setMoved] = useState(false);
  const swipe = (kind: Swipe) => {
    setMoved(true);
    deck.swipe(kind);
  };
  const undo = () => {
    setMoved(true);
    deck.undo();
  };
  const changeFilters = (next: DeckFilters) => {
    setMoved(false);
    setFilters(next);
  };

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

  const likeRefused = refusal?.kind === "like";

  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {WORDING.discover}
        </h1>
        <Link
          to="/settings/applications"
          aria-label={`A Like runs in ${MODE_WORDS[deck.mode]}. Change it in the Applications settings`}
          data-testid="deck-mode"
          className="rounded-full border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          {MODE_WORDS[deck.mode]}
        </Link>
      </header>

      <AdvancedSection pageKey="discover">
        <DeckFilterRow
          filters={filters}
          sources={sources}
          onChange={changeFilters}
        />
      </AdvancedSection>

      {deck.query.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading your deck
        </p>
      ) : deck.query.isError && !top ? (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-col items-start gap-2">
            {describeError(deck.query.error)}
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void deck.query.refetch();
              }}
            >
              {WORDING.tryAgain}
            </Button>
          </AlertDescription>
        </Alert>
      ) : top ? (
        <section aria-label={WORDING.deck} className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {deck.left === 1 ? "1 posting left" : `${deck.left} postings left`}
          </p>
          <p className="sr-only" aria-live="polite" data-testid="deck-announce">
            {moved ? `Now showing ${top.title} at ${top.company}.` : ""}
          </p>
          <SwipeCard
            key={top.id}
            card={top}
            motion={motion}
            disabled={busy}
            focusTitle={moved}
            onSwipe={swipe}
            onMore={() => {
              setMoreOpen(true);
            }}
          />
          {refusal ? (
            <Alert role="alert">
              <AlertDescription>
                {refusal.detail}
                {likeRefused ? ` ${SWIPE_TEXT.passOffered}` : ""}
              </AlertDescription>
            </Alert>
          ) : null}
          <PostingSheet card={top} open={moreOpen} onOpenChange={setMoreOpen} />
        </section>
      ) : deck.rulesUnreadable ? (
        <Alert role="status">
          <AlertDescription>
            Your roles to never apply for could not be read, so the deck shows
            nothing until they are fixed in Search options.
          </AlertDescription>
        </Alert>
      ) : deck.query.isFetchingNextPage ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading more postings
        </p>
      ) : (
        <section
          aria-label={WORDING.deck}
          className="flex flex-col gap-3 rounded-2xl border border-dashed p-6"
        >
          <p className="font-medium">{deckEmpty(deck.minFit ?? 70)}</p>
          <p className="text-sm text-muted-foreground">
            {WORDING.deckEmptyHint}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm">
              <Link to="/jobs">{WORDING.findNewPostings}</Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="/profile?tab=search">{WORDING.searchOptions}</Link>
            </Button>
          </div>
        </section>
      )}

      <div className="sticky bottom-4 flex items-center justify-center gap-4">
        <Button
          size="lg"
          variant="outline"
          className="size-16 rounded-full"
          aria-label={WORDING.pass}
          aria-keyshortcuts="ArrowLeft"
          disabled={!top || busy}
          onClick={() => {
            swipe("pass");
          }}
        >
          <X aria-hidden="true" className="size-7" />
        </Button>
        <Button
          size="lg"
          variant="ghost"
          className="min-h-11"
          aria-keyshortcuts="U"
          disabled={!last || busy}
          onClick={undo}
        >
          <Undo2 aria-hidden="true" />
          {WORDING.undoLast}
        </Button>
        <Button
          size="lg"
          className="size-16 rounded-full"
          aria-label={WORDING.like}
          aria-keyshortcuts="ArrowRight"
          disabled={!top || busy || likeRefused}
          onClick={() => {
            swipe("like");
          }}
        >
          {busy ? (
            <LoaderCircle aria-hidden="true" className="size-7 animate-spin" />
          ) : (
            <Heart aria-hidden="true" className="size-7" />
          )}
        </Button>
      </div>
      <p className="text-center text-xs text-muted-foreground">
        Keys: left arrow to pass, right arrow to like, U to undo the last one.
      </p>
    </div>
  );
}
