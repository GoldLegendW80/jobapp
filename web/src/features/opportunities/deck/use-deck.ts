import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import {
  applicationsKey,
  cancelApplication,
  getApplication,
} from "@/features/apply/apply-api";
import { likePosting, queueKey } from "@/features/apply/queue-api";
import { describeError } from "@/lib/api";

import {
  deckKey,
  getDeck,
  isGone,
  passPosting,
  refusedOf,
  undoPass,
  type Card,
  type DeckFilters,
  type DeckPage,
  type Mode,
} from "../opportunities-api";
import { LIKED, SWIPE_TEXT } from "./card-text";

/** The next page is read while this many cards or fewer are left on screen. */
export const PREFETCH_AT = 2;
/** How long the toast of a swipe, with its Undo, stays (the spec's 10 seconds). */
export const UNDO_TOAST_MS = 10_000;
/**
 * The states apply's cancel accepts (api/app/apply/gate.py, CANCELLABLE; use-deck.test.ts reads
 * that file and fails when the two differ). Past them the application is being sent or was
 * sent: the undo says it is too late and sends nothing.
 */
export const CANCELLABLE: ReadonlySet<string> = new Set([
  "queued",
  "filling",
  "needs_person",
  "ready",
  "approved",
  "held",
  "refused_by_site",
]);

export type Swipe = "like" | "pass";
export type LastSwipe =
  | { kind: "pass"; card: Card }
  | { kind: "like"; card: Card; applicationId: string };
/** A 409 of a swipe on the card shown: its sentence, kept on the card, which stays. */
export interface Refusal {
  cardId: string;
  kind: Swipe;
  detail: string;
  code: string;
}

/**
 * The deck's state: the cards in order (pages of the api, minus the cards swiped here, plus a
 * card an undo put back), one swipe in flight at a time (a gesture while one is under way is
 * ignored, so a fast double swipe never acts on a card the person has not seen), the last swipe
 * and its undo, and the refusal of the card on top.
 *
 * A passed card leaves the deck and only its undo can bring it back: no Like is ever sent for a
 * card whose Pass has not been undone first (apply's Like may accept a skipped posting, #428).
 * The truth a handler acts on lives in refs written synchronously (the cards swiped here, the
 * last swipe, the cards shown), never in a render's closure: a key handler or a drag release
 * kept from the render before a Pass's answer would otherwise still see the passed card on top.
 */
export function useDeck(filters: DeckFilters) {
  const queryClient = useQueryClient();
  const query = useInfiniteQuery<
    DeckPage,
    Error,
    InfiniteData<DeckPage>,
    ReturnType<typeof deckKey>,
    string | null
  >({
    queryKey: deckKey(filters),
    queryFn: ({ pageParam, signal }) => getDeck(filters, pageParam, signal),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    // A reload of the pages behind the person's back would move the card under the thumb.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const [restored, setRestored] = useState<readonly Card[]>([]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [last, setLast] = useState<LastSwipe | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  /** The ids swiped here and not put back: written before a swipe's `exclusive` returns. */
  const swipedRef = useRef(new Set<string>());
  /** `last`, written together with it, so an undo kept from an earlier render acts on the truth. */
  const lastRef = useRef<LastSwipe | null>(null);
  /** The id of the toast that offers the Undo of the last swipe, dismissed when it no longer does. */
  const undoToastRef = useRef<string | number | null>(null);

  const pages = query.data?.pages;
  const first = pages?.[0];
  const mode: Mode = first?.mode ?? "review";

  const cards = useMemo(() => {
    const seen = new Set<string>();
    const out: Card[] = [];
    for (const card of [
      ...restored,
      ...(pages ?? []).flatMap((p) => p.cards),
    ]) {
      if (seen.has(card.id) || removed.has(card.id)) continue;
      seen.add(card.id);
      out.push(card);
    }
    return out;
  }, [pages, restored, removed]);
  const cardsRef = useRef(cards);
  useLayoutEffect(() => {
    cardsRef.current = cards;
  }, [cards]);

  const lastPage = pages?.[pages.length - 1];
  const unread = lastPage
    ? Math.max(0, lastPage.left - lastPage.cards.length)
    : 0;
  const left = cards.length + (query.hasNextPage ? unread : 0);

  const { hasNextPage, isFetchingNextPage, fetchNextPage, isError } = query;
  useEffect(() => {
    if (
      cards.length <= PREFETCH_AT &&
      hasNextPage &&
      !isFetchingNextPage &&
      !isError
    ) {
      void fetchNextPage();
    }
  }, [cards.length, hasNextPage, isFetchingNextPage, isError, fetchNextPage]);

  const remove = useCallback((card: Card) => {
    swipedRef.current.add(card.id);
    setRemoved((now) => new Set(now).add(card.id));
    setRestored((now) => now.filter((one) => one.id !== card.id));
  }, []);
  const putBack = useCallback((card: Card) => {
    swipedRef.current.delete(card.id);
    setRemoved((now) => {
      const next = new Set(now);
      next.delete(card.id);
      return next;
    });
    setRestored((now) => [card, ...now.filter((one) => one.id !== card.id)]);
  }, []);

  /** Run one swipe or undo; false when another is already under way. */
  const exclusive = useCallback(async (work: () => Promise<void>) => {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    try {
      await work();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
    return true;
  }, []);

  const markLast = useCallback((swiped: LastSwipe | null) => {
    lastRef.current = swiped;
    setLast(swiped);
  }, []);
  const dismissUndo = () => {
    if (undoToastRef.current !== null) toast.dismiss(undoToastRef.current);
    undoToastRef.current = null;
  };
  /** The toast of `swiped`: its Undo acts only while `swiped` is still the last swipe. */
  const offerUndo = (message: string, swiped: LastSwipe) => {
    dismissUndo();
    undoToastRef.current = toast.success(message, {
      duration: UNDO_TOAST_MS,
      action: {
        label: "Undo",
        onClick: () => {
          if (lastRef.current === swiped) undo();
        },
      },
    });
  };

  const swipe = (kind: Swipe) => {
    const card = cardsRef.current[0];
    if (!card || swipedRef.current.has(card.id)) return;
    if (kind === "like" && refusal?.cardId === card.id) return;
    void exclusive(async () => {
      try {
        if (kind === "like") {
          const liked = await likePosting(card.id);
          remove(card);
          setRefusal(null);
          const swiped: LastSwipe = {
            kind,
            card,
            applicationId: liked.application.id,
          };
          markLast(swiped);
          void queryClient.invalidateQueries({ queryKey: queueKey });
          void queryClient.invalidateQueries({
            queryKey: applicationsKey(card.id),
          });
          offerUndo(LIKED[mode], swiped);
        } else {
          await passPosting(card.id);
          remove(card);
          setRefusal(null);
          const swiped: LastSwipe = { kind, card };
          markLast(swiped);
          offerUndo(SWIPE_TEXT.passed, swiped);
        }
      } catch (error) {
        const refused = refusedOf(error);
        if (isGone(error)) {
          remove(card);
          setRefusal(null);
          toast(SWIPE_TEXT.gone);
        } else if (refused) {
          setRefusal({
            cardId: card.id,
            kind,
            detail: refused.detail,
            code: refused.code,
          });
        } else {
          toast.error(describeError(error));
        }
      }
    });
  };

  function undo() {
    const swiped = lastRef.current;
    if (!swiped) return;
    void exclusive(async () => {
      if (lastRef.current !== swiped) return;
      dismissUndo();
      try {
        if (swiped.kind === "pass") {
          await undoPass(swiped.card.id);
        } else {
          const application = await getApplication(swiped.applicationId);
          if (application.state !== "cancelled") {
            if (!CANCELLABLE.has(application.state)) {
              markLast(null);
              toast.error(SWIPE_TEXT.tooLate);
              return;
            }
            await cancelApplication(swiped.applicationId);
          }
          void queryClient.invalidateQueries({ queryKey: queueKey });
          void queryClient.invalidateQueries({
            queryKey: applicationsKey(swiped.card.id),
          });
        }
        markLast(null);
        setRefusal(null);
        putBack(swiped.card);
        toast(SWIPE_TEXT.undone);
      } catch (error) {
        const refused = refusedOf(error);
        // A refusal or a vanished posting is final; a lost connection leaves the undo to retry.
        if (refused || isGone(error)) markLast(null);
        if (refused?.code === "not_cancellable")
          toast.error(SWIPE_TEXT.tooLate);
        else if (refused) toast.error(refused.detail);
        else if (isGone(error)) toast.error(SWIPE_TEXT.gone);
        else toast.error(describeError(error));
      }
    });
  }

  return {
    query,
    cards,
    top: cards[0],
    left,
    mode,
    minFit: first?.min_fit ?? null,
    rulesUnreadable: first?.rules_unreadable ?? false,
    busy,
    last,
    refusal: refusal && refusal.cardId === cards[0]?.id ? refusal : null,
    swipe,
    undo,
  };
}
