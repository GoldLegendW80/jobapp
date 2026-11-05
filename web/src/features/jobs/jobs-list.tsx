import { useWindowVirtualizer } from "@tanstack/react-virtual";
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

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { WORDING, contractWord } from "@/lib/wording";

import type { JobListItem } from "./api";
import { FitBadge, PayText, StatusBadge } from "./badges";
import { formatDate } from "./format";

/** Rows rendered beyond the visible ones, above and below. */
const OVERSCAN = 8;
/** Start fetching the next page when the window gets this close to the last loaded row. */
const PREFETCH_ROWS = 20;
const ROW_DESKTOP = 48;
const CARD_MOBILE = 172;
/** The space under each card on a phone. */
const CARD_GAP = 8;

const MOBILE_QUERY = "(max-width: 1023px)";

function subscribeMobile(onChange: () => void) {
  const media = window.matchMedia(MOBILE_QUERY);
  media.addEventListener("change", onChange);
  return () => {
    media.removeEventListener("change", onChange);
  };
}

/**
 * Below 1024px the rows are cards (taller): the virtualiser estimates with that height. Nine
 * columns, the pay among them, need a desktop width; a tablet gets the cards.
 */
function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribeMobile,
    () => window.matchMedia(MOBILE_QUERY).matches,
    () => false,
  );
}

const COLUMNS =
  "lg:grid lg:grid-cols-[1.25rem_minmax(0,1fr)_minmax(0,1.8fr)_minmax(0,1fr)_minmax(0,1.3fr)_5.5rem_6.5rem_6.5rem_5.5rem] lg:items-center lg:gap-3";

/** A header or a cell of the grid above: none of the table's own padding, and a checkbox on
 * the row's centre line. The table's elements are laid out as blocks and grids (a virtualised
 * row is positioned on its own), so their roles are said again for the screen readers that drop
 * a table's semantics with its display. */
const CELL = "p-0 [&>[role=checkbox]]:translate-y-0";

/** What the list needs to show and change the selection (jobs-page.tsx owns it). */
export interface ListSelection {
  selected: ReadonlySet<string>;
  /** A click on row `index`'s checkbox; `range` when Shift was held. */
  toggle: (index: number, range: boolean) => void;
  /** The header checkbox: everything in the current filter, or nothing. */
  allState: "none" | "some" | "all";
  toggleAll: () => void;
}

/**
 * The list: one table, virtualised against the window scroll (only the rows near the viewport
 * are in the DOM), loading the next page as the window nears the end. From 1024px up each row
 * is a table line; below, the same row is a card. Arrow keys, Home, End, Page Up and Page Down
 * move between rows; Enter opens one.
 */
export function JobsList({
  items,
  total,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  from,
  selection,
}: {
  items: JobListItem[];
  total: number;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  /** The list's query string, so the detail page can come back to the same view. */
  from: string;
  selection?: ListSelection;
}) {
  const mobile = useIsMobile();
  const navigate = useNavigate();
  // The table's parts take no ref (function components on React 18): the body and its rows are
  // found from this box.
  const boxRef = useRef<HTMLDivElement>(null);
  const measured = useRef(new WeakSet<Element>());
  const [scrollMargin, setScrollMargin] = useState(0);
  const [active, setActive] = useState(0);
  const focusPending = useRef(false);
  const estimate = mobile ? CARD_MOBILE : ROW_DESKTOP;

  useLayoutEffect(() => {
    const body = boxRef.current?.querySelector("tbody");
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
    gap: mobile ? CARD_GAP : 0,
    paddingEnd: mobile ? CARD_GAP : 0,
    getItemKey: (index) => items[index]?.id ?? index,
    // jsdom and hidden rows measure 0: keep the estimate instead of collapsing the row.
    measureElement: (element) =>
      element.getBoundingClientRect().height || estimate,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const last = virtualItems.at(-1)?.index ?? -1;

  // Each row is measured once it is in the DOM, then by the virtualiser's own observer when its
  // size changes; rows gone from the DOM are forgotten.
  useLayoutEffect(() => {
    virtualizer.measureElement(null);
    const rows =
      boxRef.current?.querySelectorAll("tbody > tr[data-index]") ?? [];
    for (const row of rows) {
      if (measured.current.has(row)) continue;
      measured.current.add(row);
      virtualizer.measureElement(row);
    }
  });

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
    const row = boxRef.current?.querySelector<HTMLElement>(
      `tbody > [data-index="${current}"]`,
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

  const onKeyDown = (
    event: KeyboardEvent<HTMLTableRowElement>,
    index: number,
  ) => {
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
    if (
      selection &&
      (event.key === " " || event.key === "x") &&
      event.target === event.currentTarget
    ) {
      event.preventDefault();
      selection.toggle(index, event.shiftKey);
      return;
    }
    if (event.key === "Enter" && event.target === event.currentTarget) {
      event.preventDefault();
      const job = items[index];
      if (job) open(job);
    }
  };

  const onRowClick = (
    event: MouseEvent<HTMLTableRowElement>,
    job: JobListItem,
  ) => {
    // The title link navigates on its own (and keeps middle click and new tab); the checkbox
    // selects.
    if ((event.target as HTMLElement).closest("a, button, input, label"))
      return;
    open(job);
  };

  return (
    // The rows draw their own cards under 1024px: the table's border shows from there up only.
    <div
      ref={boxRef}
      className="[&>[data-slot=table-container]]:max-lg:rounded-none [&>[data-slot=table-container]]:max-lg:border-0"
    >
      <Table
        stacked={false}
        role="table"
        aria-label="Postings"
        aria-rowcount={total + 1}
        className="block"
      >
        <TableHeader role="rowgroup" className="block max-lg:sr-only">
          <TableRow
            role="row"
            aria-rowindex={1}
            className={cn(
              COLUMNS,
              "bg-muted/50 px-3 py-2 text-left text-xs font-medium text-muted-foreground hover:bg-muted/50",
            )}
          >
            <TableHead
              role="columnheader"
              className={cn(CELL, "flex h-auto items-center")}
            >
              {selection ? (
                <HeaderCheckbox selection={selection} />
              ) : (
                <span className="sr-only">Select</span>
              )}
            </TableHead>
            {[
              "Company",
              "Title",
              "Location",
              WORDING.pay,
              "Type",
              "Fit",
              "Status",
              "Found",
            ].map((name) => (
              <TableHead
                key={name}
                role="columnheader"
                className={cn(CELL, "h-auto whitespace-normal text-inherit")}
              >
                {name}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody
          role="rowgroup"
          className="relative block"
          style={{ height: virtualizer.getTotalSize() }}
        >
          {virtualItems.map((virtual) => {
            const job = items[virtual.index];
            if (!job) return null;
            const checked = selection?.selected.has(job.id) ?? false;
            return (
              <TableRow
                key={virtual.key}
                data-index={virtual.index}
                role="row"
                aria-rowindex={virtual.index + 2}
                aria-selected={selection ? checked : undefined}
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
                className={cn(
                  "absolute top-0 left-0 w-full cursor-pointer outline-none",
                  COLUMNS,
                  "flex flex-wrap items-center gap-x-2 gap-y-1 transition-colors hover:bg-accent/60 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset",
                  // A card keeps its border when it is the last row (the body clears that one).
                  "max-lg:rounded-lg max-lg:border! max-lg:bg-card max-lg:p-3",
                  "lg:h-12 lg:px-3",
                  checked && "bg-accent/40",
                )}
                style={{
                  transform: `translateY(${virtual.start - scrollMargin}px)`,
                }}
              >
                <TableCell
                  role="cell"
                  className={cn(CELL, "flex items-center max-lg:order-first")}
                >
                  {selection ? (
                    <Checkbox
                      checked={checked}
                      aria-label={`Select ${job.title} at ${job.company}`}
                      tabIndex={-1}
                      className="cursor-pointer"
                      onClick={(event) => {
                        event.stopPropagation();
                        selection.toggle(virtual.index, event.shiftKey);
                      }}
                    />
                  ) : null}
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "truncate text-sm text-muted-foreground max-lg:min-w-0 max-lg:flex-1 max-lg:text-xs lg:text-foreground",
                  )}
                >
                  {job.company}
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "min-w-0 text-sm font-medium whitespace-normal max-lg:basis-full",
                  )}
                >
                  <Link
                    to={`/jobs/${job.id}`}
                    state={{ from }}
                    tabIndex={-1}
                    className="line-clamp-2 break-words hover:underline lg:line-clamp-1"
                  >
                    {job.title}
                  </Link>
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "truncate text-sm text-muted-foreground max-lg:basis-full max-lg:text-xs",
                  )}
                >
                  {job.location}
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "text-sm font-medium whitespace-normal max-lg:basis-full lg:line-clamp-2 lg:text-xs",
                  )}
                >
                  <PayText job={job} />
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "text-xs whitespace-normal text-muted-foreground lg:line-clamp-2",
                  )}
                >
                  {contractWord(job.contract_type)}
                </TableCell>
                <TableCell role="cell" className={CELL}>
                  <FitBadge fit={job.fit} overridden={job.fit_overridden} />
                </TableCell>
                <TableCell role="cell" className={CELL}>
                  <StatusBadge status={job.status} />
                </TableCell>
                <TableCell
                  role="cell"
                  className={cn(
                    CELL,
                    "text-xs text-muted-foreground tabular-nums max-lg:ml-auto",
                  )}
                >
                  <time dateTime={job.found_on}>
                    {formatDate(job.found_on)}
                  </time>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {hasNextPage ? (
        <p
          role="status"
          aria-live="polite"
          className="flex items-center justify-center gap-2 px-3 py-3 text-sm text-muted-foreground"
        >
          {isFetchingNextPage ? (
            <>
              <Spinner aria-hidden="true" role={undefined} />
              Loading more postings
            </>
          ) : (
            <Button
              type="button"
              variant="link"
              className="h-auto p-0 font-normal text-inherit underline has-[>svg]:px-0"
              onClick={fetchNextPage}
            >
              Load more postings
            </Button>
          )}
        </p>
      ) : null}
    </div>
  );
}

function HeaderCheckbox({ selection }: { selection: ListSelection }) {
  return (
    <Checkbox
      className="cursor-pointer"
      checked={
        selection.allState === "all"
          ? true
          : selection.allState === "some"
            ? "indeterminate"
            : false
      }
      aria-label={
        selection.allState === "none"
          ? "Select every posting in this view"
          : "Clear the selection"
      }
      onCheckedChange={selection.toggleAll}
    />
  );
}

/** Placeholder rows while the first page loads. */
export function JobsListSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading postings"
      className="flex flex-col gap-2 lg:gap-0 lg:rounded-lg lg:border"
    >
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="flex flex-col gap-2 max-lg:rounded-lg max-lg:border max-lg:p-3 lg:h-12 lg:flex-row lg:items-center lg:gap-3 lg:border-b lg:px-3 lg:last:border-b-0"
        >
          <Skeleton className="h-3 w-24 rounded bg-muted" />
          <Skeleton className="h-3 w-48 rounded bg-muted lg:flex-1" />
          <Skeleton className="h-3 w-28 rounded bg-muted" />
          <Skeleton className="h-4 w-20 rounded-full bg-muted" />
        </div>
      ))}
    </div>
  );
}
