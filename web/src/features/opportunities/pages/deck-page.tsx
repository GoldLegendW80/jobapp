import {
  ArrowLeft,
  ArrowRight,
  CircleAlert,
  Heart,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";
import { useJobCount } from "@/features/jobs/api/use-jobs";
import { useProfile } from "@/features/profile/api/use-profile";
import {
  DECK_NO_SKILLS_HINT,
  WORDING,
  deckEmpty,
  deckNoSkills,
} from "@/lib/wording";

import { NO_FILTERS, type DeckFilters } from "../api/opportunities-api";
import { MODE_WORDS, SWIPE_TEXT } from "../helpers/deck/card-text";
import { DeckFilterRow } from "../components/deck/filter-row";
import { PostingSheet } from "../components/deck/posting-sheet";
import { SwipeCard } from "../components/deck/swipe-card";
import { useDeck, type Swipe } from "../api/deck/use-deck";
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

/**
 * /discover, the swipe deck (SPEC-opportunities.md, "The swipe deck"): one card at a time, Like
 * and Pass by drag, by the buttons or by the arrow keys (left Pass, right Like, U undoes the
 * last swipe), Undo last until the next swipe, the mode a Like runs in, the filter
 * row under Advanced and the empty state naming the person's threshold. Phone width first: the
 * buttons sit under the card, within a thumb's reach, each named under it. Without a card, Pass
 * and Like are gone and Undo last stays only while there is a swipe to undo; the keys are offered
 * only to a precise pointer.
 */
export function DeckPage() {
  useTitle(WORDING.discover);
  const [filters, setFilters] = useState<DeckFilters>(NO_FILTERS);
  const deck = useDeck(filters);
  const motion = !useReducedMotion();
  // The keys are offered where a keyboard is likely: a mouse or a trackpad, not a touch screen.
  const finePointer = useFinePointer();
  const passId = useId();
  const likeId = useId();
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
      <PageHeader
        className="items-center"
        title={WORDING.discover}
        actions={
          <Badge variant="outline" asChild>
            <Link
              to="/settings/applications"
              aria-label={`A Like runs in ${MODE_WORDS[deck.mode]}. Change it in the Applications settings`}
              data-testid="deck-mode"
              className="px-3 py-1 text-muted-foreground"
            >
              {`${WORDING.like}: ${MODE_WORDS[deck.mode]}`}
            </Link>
          </Badge>
        }
      />

      <AdvancedSection pageKey="discover">
        <DeckFilterRow
          filters={filters}
          sources={sources}
          onChange={changeFilters}
        />
      </AdvancedSection>

      {deck.query.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner aria-hidden="true" role={undefined} />
          Loading your deck
        </p>
      ) : deck.query.isError && !top ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
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
          <Spinner aria-hidden="true" role={undefined} />
          Loading more postings
        </p>
      ) : (
        <DeckEmpty
          threshold={deck.minFit ?? 70}
          filtered={
            filters.types.length > 0 ||
            filters.remote !== null ||
            filters.source !== null
          }
        />
      )}

      {top || last ? (
        <div
          data-testid="deck-controls"
          className="flex items-start justify-center gap-4"
        >
          {top ? (
            <div className="flex flex-col items-center gap-1.5">
              <Button
                id={passId}
                size="lg"
                variant="outline"
                className="size-16 rounded-full"
                aria-label={WORDING.pass}
                aria-keyshortcuts="ArrowLeft"
                disabled={busy}
                onClick={() => {
                  swipe("pass");
                }}
              >
                <X aria-hidden="true" className="size-7" />
              </Button>
              <Label
                htmlFor={passId}
                className="cursor-pointer leading-5 text-muted-foreground"
              >
                {WORDING.pass}
              </Label>
            </div>
          ) : null}
          <div className="flex h-16 items-center">
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
          </div>
          {top ? (
            <div className="flex flex-col items-center gap-1.5">
              <Button
                id={likeId}
                size="lg"
                className="size-16 rounded-full"
                aria-label={WORDING.like}
                aria-keyshortcuts="ArrowRight"
                disabled={busy || likeRefused}
                onClick={() => {
                  swipe("like");
                }}
              >
                {busy ? (
                  <Spinner
                    aria-hidden="true"
                    role={undefined}
                    className="size-7"
                  />
                ) : (
                  <Heart aria-hidden="true" className="size-7" />
                )}
              </Button>
              <Label
                htmlFor={likeId}
                className="cursor-pointer leading-5 text-muted-foreground"
              >
                {WORDING.like}
              </Label>
            </div>
          ) : null}
        </div>
      ) : null}
      {top && finePointer ? (
        <p
          data-testid="deck-keys"
          className="text-center text-xs text-muted-foreground"
        >
          Keys:{" "}
          <Kbd>
            <ArrowLeft aria-hidden="true" />
            <span className="sr-only">left arrow</span>
          </Kbd>{" "}
          to pass,{" "}
          <Kbd>
            <ArrowRight aria-hidden="true" />
            <span className="sr-only">right arrow</span>
          </Kbd>{" "}
          to like, <Kbd>U</Kbd> to undo the last one.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The empty deck, said as its cause: no posting at all (the list of websites is the way in), a
 * profile without any skill (every fit is near 0, under the threshold), or postings under the
 * threshold. With a filter on, the threshold line stays, since the cause may be the filter. Each
 * cause has the action that fixes it; while the counts load, the threshold line shows.
 */
function DeckEmpty({
  threshold,
  filtered,
}: {
  threshold: number;
  filtered: boolean;
}) {
  const postings = useJobCount({ enabled: !filtered });
  const profile = useProfile({ enabled: !filtered });
  const noPostings = !filtered && postings.data === 0;
  const noSkills =
    !filtered &&
    !noPostings &&
    profile.data !== undefined &&
    profile.data.sections.skills.have.every(
      (category) => category.terms.length === 0,
    );
  const [title, hint, actions] = noPostings
    ? [
        WORDING.deckNoPostings,
        WORDING.deckNoPostingsHint,
        <Button key="find" asChild size="sm">
          <Link to="/jobs?find=1">{WORDING.findJobs}</Link>
        </Button>,
      ]
    : noSkills
      ? [
          deckNoSkills(threshold),
          DECK_NO_SKILLS_HINT,
          <>
            <Button asChild size="sm">
              <Link to="/profile?tab=skills">{WORDING.addSkills}</Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="/settings/search">{WORDING.searchOptions}</Link>
            </Button>
          </>,
        ]
      : [
          deckEmpty(threshold),
          WORDING.deckEmptyHint,
          <>
            <Button asChild size="sm">
              <Link to="/jobs?find=1">{WORDING.findNewPostings}</Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="/settings/search">{WORDING.searchOptions}</Link>
            </Button>
          </>,
        ];
  return (
    <section aria-label={WORDING.deck}>
      <Empty className="gap-4 rounded-xl bg-card p-6 md:p-6">
        <EmptyHeader className="gap-2">
          <EmptyTitle className="text-base tracking-normal">{title}</EmptyTitle>
          <EmptyDescription>{hint}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="flex-row flex-wrap justify-center gap-2">
          {actions}
        </EmptyContent>
      </Empty>
    </section>
  );
}
