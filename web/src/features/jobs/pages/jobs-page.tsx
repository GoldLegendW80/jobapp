import { Maximize2, SearchX } from "lucide-react";
import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useDefaultLayout, type LayoutStorage } from "react-resizable-panels";
import { Link, useSearchParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { FiltersToolbar } from "@/components/shared/jobs/filters/filters-toolbar";
import { JobsFrame } from "@/components/shared/jobs/jobs-frame";
import { JobPosting } from "@/components/shared/jobs/posting/job-posting";
import { useIsPhone, useMediaQuery } from "@/hooks/use-media-query";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { listQuery } from "../api/jobs-api";
import { useJobs } from "../api/use-jobs";
import { useSweepRunner } from "../api/use-sweep-runner";
import { SweepProgress } from "@/components/shared/jobs/find-jobs";
import { HeaderActions } from "@/components/shared/jobs/jobs-actions/header-actions";
import { JobsList } from "../components/jobs-list/jobs-list";
import { JobsListSkeleton } from "../components/jobs-list/jobs-list-skeleton";
import { NoPostingsYet } from "@/components/shared/jobs/no-postings-yet";
import {
  EMPTY_FILTERS,
  hasFilters,
  parseSelected,
  SELECTED_PARAM,
  withSelected,
} from "../helpers/filters";
import { plural } from "../helpers/selection";
import { useFindJobs } from "../hooks/use-find-jobs";
import { useJobFilters } from "../hooks/use-job-filters";
import { useSelection } from "../hooks/use-selection";

/** Below Tailwind's `lg` (1024px): the list alone, without the detail pane. */
const NARROW_QUERY = "(max-width: 1023px)";

/** The two panes fill the screen under the app's sticky header (4rem) and a margin. */
const PANES = "h-[calc(100dvh-6rem)] min-h-[30rem]";

/**
 * /jobs, the home page, the List view of Jobs: a toolbar (search, status toggles with their
 * counts, and the other filters in a sheet, all in the URL) and the postings, loaded page by page
 * as they scroll. From 1024px the list and the selected posting sit side by side in resizable
 * panes, each scrolling on its own, the selection in the URL (`job`, the first posting when none); below, the list
 * alone, a tap opening the posting's page. Postings can be ticked (Shift for a range, the box on
 * top for the whole view) for the bulk bar; the header finds jobs on the person's list of places
 * (live progress, then a summary), adds a posting by hand, and its menu holds the automatic
 * search, the past searches and rescoring. With no posting at all and no filter, the view says how
 * to get postings and holds Find jobs and Add a posting itself, without the toolbar.
 */
export function JobsPage() {
  useTitle("Jobs");
  const { filters, setFilters, query } = useJobFilters();
  const [params, setParams] = useSearchParams();
  const apiQuery = listQuery(filters);
  const list = useJobs(apiQuery);
  const first = list.data?.pages[0];
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = hasFilters(filters);
  const { fetchNextPage, hasNextPage, isFetchingNextPage } = list;
  const loadMore = useCallback(() => {
    void fetchNextPage();
  }, [fetchNextPage]);
  const selection = useSelection(items, apiQuery, first?.total ?? 0);
  const sweep = useSweepRunner();
  const finder = useFindJobs(sweep);
  const narrow = useMediaQuery(NARROW_QUERY);
  const phone = useIsPhone();
  // A phone is narrow too; asking both keeps a screen that only answers the phone query on the list.
  const wide = !narrow && !phone;
  const currentId = parseSelected(params) ?? items[0]?.id ?? null;
  const select = useCallback(
    (id: string) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          next.set(SELECTED_PARAM, id);
          return next;
        },
        // Moving through the list is not a new view: back leaves the list, it does not replay it.
        { replace: true },
      );
    },
    [setParams],
  );
  const from = query ? `?${query}` : "";
  // Nothing to count or filter yet: no posting at all, and no filter that hides some.
  const empty = first !== undefined && first.total === 0 && !filtered;

  return (
    <JobsFrame
      query={query}
      description={
        <span role="status" aria-live="polite" className="tabular-nums">
          {first
            ? `${plural(first.total, "posting")}${filtered ? " match" : ""}`
            : ""}
        </span>
      }
      actions={
        <HeaderActions finder={finder} selected={selection.ids} empty={empty} />
      }
    >
      <SweepProgress runner={sweep} />

      {empty ? (
        <NoPostingsYet finder={finder} />
      ) : (
        <FiltersToolbar
          filters={filters}
          onChange={setFilters}
          summary={first}
          unavailable={list.isError && !first}
        />
      )}

      {empty ? null : (
        <div aria-busy={list.isFetching && !isFetchingNextPage}>
          {list.isPending ? (
            <Panes
              wide={wide}
              list={<JobsListSkeleton />}
              pane={<PaneSkeleton />}
            />
          ) : list.isError && !first ? (
            <LoadError query={list}>
              The postings could not be loaded.
            </LoadError>
          ) : first && first.total === 0 ? (
            <EmptyState
              icon={SearchX}
              title="No postings match these filters"
              description="Change a filter, or clear them to see every posting."
              actions={
                <Button
                  variant="outline"
                  onClick={() => {
                    setFilters({ ...EMPTY_FILTERS, sort: filters.sort });
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          ) : (
            <Panes
              wide={wide}
              list={
                <>
                  {list.isError ? (
                    <LoadError
                      query={{
                        error: list.error,
                        isFetching: list.isFetching,
                        refetch: loadMore,
                      }}
                    >
                      More postings could not be loaded.
                    </LoadError>
                  ) : null}
                  <JobsList
                    items={items}
                    total={first?.total ?? items.length}
                    hasNextPage={hasNextPage && !list.isError}
                    isFetchingNextPage={isFetchingNextPage}
                    fetchNextPage={loadMore}
                    from={from}
                    selection={selection}
                    current={
                      wide ? { id: currentId, onSelect: select } : undefined
                    }
                  />
                </>
              }
              pane={
                currentId ? (
                  <PostingPane
                    id={currentId}
                    from={withSelected(from, currentId)}
                  />
                ) : null
              }
            />
          )}
        </div>
      )}
      <BulkBar selection={selection} />
    </JobsFrame>
  );
}

/** The width the person gave the list, kept in this browser; a blocked storage keeps it for the visit. */
const PANES_STORAGE: LayoutStorage = {
  getItem: (key) => {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem: (key, value) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Storage blocked or full: the width only lasts for this visit.
    }
  },
};

/**
 * The list, and from 1024px the detail pane beside it, both as tall as the screen allows. The
 * border between them is a handle: dragged, or moved with the arrow keys once focused, it shares
 * the width (the list from 20rem to 60%), and a double click puts it back.
 */
function Panes({
  wide,
  list,
  pane,
}: {
  wide: boolean;
  list: ReactNode;
  pane: ReactNode;
}) {
  const layout = useDefaultLayout({
    id: "jobs-panes",
    storage: PANES_STORAGE,
    onlySaveAfterUserInteractions: true,
  });
  // A group not laid out yet (jsdom) measures 0 wide, so its handle would catch every click on the page.
  const [laidOut, setLaidOut] = useState(false);
  const measure = useCallback((element: HTMLDivElement | null) => {
    if (element && element.getBoundingClientRect().width > 0) setLaidOut(true);
  }, []);
  // The group sizes itself to its box (an inline 100%): the box is as tall as the screen allows.
  return wide ? (
    <div className={PANES}>
      <ResizablePanelGroup
        orientation="horizontal"
        elementRef={measure}
        disabled={!laidOut}
        defaultLayout={layout.defaultLayout}
        onLayoutChanged={layout.onLayoutChanged}
      >
        <ResizablePanel
          id="list"
          defaultSize="26rem"
          minSize="20rem"
          maxSize="60%"
          className="flex min-h-0 flex-col gap-3 pr-2"
        >
          {list}
        </ResizablePanel>
        <ResizableHandle withHandle aria-label="Width of the list" />
        <ResizablePanel
          id="posting"
          minSize="24rem"
          className="flex min-h-0 flex-col pl-2"
        >
          {pane}
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  ) : (
    <div className="flex flex-col gap-3">{list}</div>
  );
}

/**
 * The selected posting, as its own page shows it (job-posting.tsx), scrolled back to its top when
 * the selection changes, with a way to its full page.
 */
function PostingPane({ id, from }: { id: string; from: string }) {
  const ref = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = 0;
  }, [id]);
  return (
    <section
      ref={ref}
      aria-label="Selected posting"
      className="min-h-0 overflow-y-auto pr-1"
    >
      <JobPosting
        key={id}
        id={id}
        titleLevel={2}
        actions={
          <Button asChild variant="outline">
            <Link to={`/jobs/${id}`} state={{ from }}>
              <Maximize2 aria-hidden="true" />
              {WORDING.openFullPage}
            </Link>
          </Button>
        }
      />
    </section>
  );
}

function PaneSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="flex flex-col gap-4 rounded-xl bg-card p-6"
    >
      <Skeleton className="h-3.5 w-32 rounded bg-muted" />
      <Skeleton className="h-7 w-2/3 rounded bg-muted" />
      <Skeleton className="h-3.5 w-40 rounded bg-muted" />
      <Skeleton className="h-6 w-48 rounded bg-muted" />
      <div className="flex gap-2">
        <Skeleton className="h-5 w-20 rounded-full bg-muted" />
        <Skeleton className="h-5 w-16 rounded-full bg-muted" />
      </div>
      <Skeleton className="mt-4 h-40 w-full rounded-lg bg-muted" />
    </div>
  );
}
