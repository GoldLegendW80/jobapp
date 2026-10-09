import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { LoaderCircle } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Link, useNavigate } from "react-router";

import { cn } from "@/lib/utils";

import type { JobListItem } from "./api";
import { FitBadge, StatusBadge } from "./badges";
import { formatDate } from "./format";

/** Rows rendered beyond the visible ones, above and below. */
const OVERSCAN = 8;
/** Start fetching the next page when the window gets this close to the last loaded row. */
const PREFETCH_ROWS = 20;
const ROW_DESKTOP = 48;
const CARD_MOBILE = 148;

const MOBILE_QUERY = "(max-width: 639px)";

function subscribeMobile(onChange: () => void) {
  const media = window.matchMedia(MOBILE_QUERY);
  media.addEventListener("change", onChange);
  return () => {
    media.removeEventListener("change", onChange);
  };
}

/** Below 640px the rows are cards (taller): the virtualiser estimates with that height. */
function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribeMobile,
    () => window.matchMedia(MOBILE_QUERY).matches,
    () => false,
  );
}

const COLUMNS =
  "sm:grid sm:grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)_minmax(0,1.2fr)_4.5rem_5.5rem_7rem_6.5rem] sm:items-center sm:gap-3";

/**
 * The list: one ARIA table, virtualised against the window scroll (only the rows near the
 * viewport are in the DOM), loading the next page as the window nears the end. From 640px up
 * each row is a table line; below, the same row is a card. Arrow keys, Home, End, Page Up and
 * Page Down move between rows; Enter opens one.
 */
export function JobsList({
  items,
  total,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  from,
}: {
  items: JobListItem[];
  total: number;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  /** The list's query string, so the detail page can come back to the same view. */
  from: string;
}) {
  const mobile = useIsMobile();
  const navigate = useNavigate();
  const bodyRef = useRef<HTMLDivElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const [active, setActive] = useState(0);
  const focusPending = useRef(false);
  const estimate = mobile ? CARD_MOBILE : ROW_DESKTOP;

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const measure = () => {
      setScrollMargin(body.getBoundingClientRect().top + window.scrollY);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("resize", measure);
    };
  }, [mobile]);

  const virtualizer = useWindowVirtualizer({
    count: items.length,
    estimateSize: () => estimate,
    overscan: OVERSCAN,
    scrollMargin,
    getItemKey: (index) => items[index]?.id ?? index,
    // jsdom and hidden rows measure 0: keep the estimate instead of collapsing the row.
    measureElement: (element) =>
      element.getBoundingClientRect().height || estimate,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const last = virtualItems.at(-1)?.index ?? -1;

  useEffect(() => {
    if (
      hasNextPage &&
      !isFetchingNextPage &&
      last >= items.length - PREFETCH_ROWS
    ) {
      fetchNextPage();
    }
  }, [last, items.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const current = Math.min(active, Math.max(0, items.length - 1));

  useEffect(() => {
    if (!focusPending.current) return;
    const row = bodyRef.current?.querySelector<HTMLElement>(
      `[data-index="${current}"]`,
    );
    if (row) {
      focusPending.current = false;
      row.focus({ preventScroll: true });
    }
  });

  const open = useCallback(
    (job: JobListItem) => {
      void navigate(`/jobs/${job.id}`, { state: { from } });
    },
    [navigate, from],
  );

  const move = (index: number) => {
    const next = Math.max(0, Math.min(items.length - 1, index));
    setActive(next);
    focusPending.current = true;
    virtualizer.scrollToIndex(next, { align: "auto" });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>, index: number) => {
    const page = Math.max(1, Math.floor(window.innerHeight / estimate) - 1);
    const keys: Record<string, number> = {
      ArrowDown: index + 1,
      ArrowUp: index - 1,
      Home: 0,
      End: items.length - 1,
      PageDown: index + page,
      PageUp: index - page,
    };
    const target = keys[event.key];
    if (target !== undefined) {
      event.preventDefault();
      move(target);
      return;
    }
    if (event.key === "Enter" && event.target === event.currentTarget) {
      event.preventDefault();
      const job = items[index];
      if (job) open(job);
    }
  };

  const onRowClick = (event: MouseEvent<HTMLDivElement>, job: JobListItem) => {
    // The title link navigates on its own (and keeps middle click and new tab).
    if ((event.target as HTMLElement).closest("a")) return;
    open(job);
  };

  return (
    <div
      role="table"
      aria-label="Postings"
      aria-rowcount={total + 1}
      className="sm:rounded-lg sm:border"
    >
      <div role="rowgroup" className="max-sm:sr-only">
        <div
          role="row"
          aria-rowindex={1}
          className={cn(
            COLUMNS,
            "border-b bg-muted/50 px-3 py-2 text-left text-xs font-medium text-muted-foreground",
          )}
        >
          <span role="columnheader">Company</span>
          <span role="columnheader">Title</span>
          <span role="columnheader">Location</span>
          <span role="columnheader">Type</span>
          <span role="columnheader">Fit</span>
          <span role="columnheader">Status</span>
          <span role="columnheader">Found</span>
        </div>
      </div>
      <div
        ref={bodyRef}
        role="rowgroup"
        className="relative"
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualItems.map((virtual) => {
          const job = items[virtual.index];
          if (!job) return null;
          return (
            <div
              key={virtual.key}
              ref={virtualizer.measureElement}
              data-index={virtual.index}
              role="row"
              aria-rowindex={virtual.index + 2}
              tabIndex={virtual.index === current ? 0 : -1}
              onKeyDown={(event) => {
                onKeyDown(event, virtual.index);
              }}
              onFocus={() => {
                setActive(virtual.index);
              }}
              onClick={(event) => {
                onRowClick(event, job);
              }}
              className="group absolute top-0 left-0 w-full cursor-pointer outline-none max-sm:pb-2"
              style={{
                transform: `translateY(${virtual.start - scrollMargin}px)`,
              }}
            >
              <div
                className={cn(
                  COLUMNS,
                  "flex flex-wrap items-center gap-x-2 gap-y-1 transition-colors group-hover:bg-accent/60 group-focus-visible:ring-[3px] group-focus-visible:ring-ring/50 group-focus-visible:ring-inset",
                  "max-sm:rounded-lg max-sm:border max-sm:bg-card max-sm:p-3",
                  "sm:h-12 sm:border-b sm:px-3",
                )}
              >
                <span
                  role="cell"
                  className="truncate text-sm text-muted-foreground max-sm:basis-full max-sm:text-xs sm:text-foreground"
                >
                  {job.company}
                </span>
                <span
                  role="cell"
                  className="min-w-0 text-sm font-medium max-sm:basis-full"
                >
                  <Link
                    to={`/jobs/${job.id}`}
                    state={{ from }}
                    tabIndex={-1}
                    className="line-clamp-2 break-words hover:underline sm:line-clamp-1"
                  >
                    {job.title}
                  </Link>
                </span>
                <span
                  role="cell"
                  className="truncate text-sm text-muted-foreground max-sm:basis-full max-sm:text-xs"
                >
                  {job.location}
                </span>
                <span role="cell" className="text-xs text-muted-foreground">
                  {job.contract_type}
                </span>
                <span role="cell">
                  <FitBadge fit={job.fit} overridden={job.fit_overridden} />
                </span>
                <span role="cell">
                  <StatusBadge status={job.status} />
                </span>
                <span
                  role="cell"
                  className="text-xs text-muted-foreground tabular-nums max-sm:ml-auto"
                >
                  <time dateTime={job.found_on}>
                    {formatDate(job.found_on)}
                  </time>
                </span>
              </div>
            </div>
          );
        })}
      </div>
      {hasNextPage ? (
        <p
          role="status"
          aria-live="polite"
          className="flex items-center justify-center gap-2 px-3 py-3 text-sm text-muted-foreground"
        >
          {isFetchingNextPage ? (
            <>
              <LoaderCircle
                aria-hidden="true"
                className="size-4 animate-spin"
              />
              Loading more postings
            </>
          ) : (
            <button
              type="button"
              className="underline underline-offset-4"
              onClick={fetchNextPage}
            >
              Load more postings
            </button>
          )}
        </p>
      ) : null}
    </div>
  );
}

/** Placeholder rows while the first page loads. */
export function JobsListSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading postings"
      className="flex flex-col gap-2 sm:gap-0 sm:rounded-lg sm:border"
    >
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="flex animate-pulse flex-col gap-2 max-sm:rounded-lg max-sm:border max-sm:p-3 sm:h-12 sm:flex-row sm:items-center sm:gap-3 sm:border-b sm:px-3 sm:last:border-b-0"
        >
          <span className="h-3 w-24 rounded bg-muted" />
          <span className="h-3 w-48 rounded bg-muted sm:flex-1" />
          <span className="h-3 w-28 rounded bg-muted" />
          <span className="h-4 w-20 rounded-full bg-muted" />
        </div>
      ))}
    </div>
  );
}
