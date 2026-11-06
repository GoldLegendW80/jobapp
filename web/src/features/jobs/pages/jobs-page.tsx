import {
  Briefcase,
  CircleAlert,
  Maximize2,
  RotateCw,
  SearchX,
} from "lucide-react";
import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { FinishSetupCard } from "@/components/shared/onboarding/onboarding-gate";
import { describeError } from "@/lib/api";
import { useIsPhone, useMediaQuery } from "@/hooks/use-media-query";
import { useTitle } from "@/hooks/use-title";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { listQuery } from "../api/jobs-api";
import { useJobs } from "../api/use-jobs";
import {
  FindJobsButton,
  FindJobsSetup,
  PastSearchesButton,
  SweepProgress,
  SweepSummaryDialog,
} from "../components/find-jobs";
import { AddPostingButton } from "../components/jobs-actions/add-posting-button";
import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { PageMenu } from "../components/jobs-actions/page-menu";
import { ScheduleButton } from "../components/schedule-dialog";
import {
  EMPTY_FILTERS,
  hasFilters,
  parseSelected,
  SELECTED_PARAM,
  toggleOnlyStatus,
  withSelected,
} from "../helpers/filters";
import { FiltersToolbar } from "../components/filters/filters-toolbar";
import { JobPosting } from "@/components/shared/jobs/posting/job-posting";
import { JobsViewTabs } from "@/components/shared/jobs/map/view-tabs";
import { JobsList } from "../components/jobs-list/jobs-list";
import { JobsListSkeleton } from "../components/jobs-list/jobs-list-skeleton";
import { KpiTiles } from "../components/kpi-tiles";
import { plural } from "../helpers/selection";
import { useSelection } from "../hooks/use-selection";
import { useFindJobs, type FindJobs } from "../hooks/use-find-jobs";
import { useSweepRunner } from "../api/use-sweep-runner";
import { useJobFilters } from "../hooks/use-job-filters";

/** Below Tailwind's `lg` (1024px): the list alone, without the detail pane. */
const NARROW_QUERY = "(max-width: 1023px)";

/** The two panes fill the screen under the app's sticky header (4rem) and a margin. */
const PANES = "h-[calc(100dvh-6rem)] min-h-[30rem]";

/**
 * /jobs, the home page: counts per status, a toolbar (search, and the other filters in a sheet,
 * all in the URL) and the postings, loaded page by page as they scroll. From 1024px the list and
 * the selected posting sit side by side, each scrolling on its own, the selection in the URL
 * (`job`, the first posting when none); below, the list alone, a tap opening the posting's page.
 * Postings can be ticked (Shift for a range, the box on top for the whole view) for the bulk bar;
 * the header finds jobs on the person's list of places (live progress, then a summary), adds a
 * posting by hand, and its menu rescores. With no posting at all and no filter, the list says how
 * to get postings and holds Find jobs and Add a posting itself, without the tiles and the toolbar.
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
    <div className="mx-auto flex max-w-6xl flex-col gap-4 sm:gap-6">
      <JobsViewTabs current="list" query={query} />
      <FinishSetupCard />
      <PageHeader
        title="Jobs"
        description={
          <span role="status" aria-live="polite" className="tabular-nums">
            {first
              ? `${plural(first.total, "posting")}${filtered ? " match" : ""}`
              : ""}
          </span>
        }
        actions={
          <>
            {/* The empty list holds Find jobs and Add a posting itself. */}
            {empty ? null : <FindJobsButton finder={finder} />}
            <ScheduleButton />
            <PastSearchesButton runner={sweep} />
            {empty ? null : <AddPostingButton />}
            <PageMenu selected={selection.ids} />
          </>
        }
      />

      <FindJobsSetup finder={finder} />
      <SweepProgress runner={sweep} />
      <SweepSummaryDialog runner={sweep} />

      {empty ? null : (
        <>
          <KpiTiles
            counts={first?.counts_by_status}
            selected={filters.statuses}
            loading={!first}
            unavailable={list.isError && !first}
            onSelect={(status) => {
              setFilters(toggleOnlyStatus(filters, status));
            }}
          />
          <FiltersToolbar
            filters={filters}
            onChange={setFilters}
            total={first ? first.total : null}
            histogram={first?.fit_histogram}
          />
        </>
      )}

      <div aria-busy={list.isFetching && !isFetchingNextPage}>
        {list.isPending ? (
          <Panes
            wide={wide}
            list={<JobsListSkeleton />}
            pane={<PaneSkeleton />}
          />
        ) : list.isError && !first ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription className="flex flex-col gap-3">
              <p>
                The postings could not be loaded. {describeError(list.error)}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => void list.refetch()}
                disabled={list.isFetching}
              >
                <RotateCw aria-hidden="true" />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : first && first.total === 0 ? (
          filtered ? (
            <Empty className="gap-2 rounded-xl bg-card px-4 py-10 md:px-4 md:py-10">
              <EmptyHeader className="gap-2">
                <EmptyMedia className="mb-0 text-muted-foreground">
                  <SearchX aria-hidden="true" className="size-6" />
                </EmptyMedia>
                <EmptyTitle className="text-base tracking-normal">
                  No postings match these filters
                </EmptyTitle>
              </EmptyHeader>
              <EmptyContent>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setFilters({ ...EMPTY_FILTERS, sort: filters.sort });
                  }}
                >
                  Clear filters
                </Button>
              </EmptyContent>
            </Empty>
          ) : (
            <EmptyList finder={finder} />
          )
        ) : (
          <Panes
            wide={wide}
            list={
              <>
                {list.isError ? (
                  <Alert variant="destructive">
                    <CircleAlert aria-hidden="true" />
                    <AlertDescription className="flex flex-wrap items-center gap-3">
                      <p>
                        More postings could not be loaded.{" "}
                        {describeError(list.error)}
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={loadMore}
                        disabled={list.isFetching}
                      >
                        <RotateCw aria-hidden="true" />
                        Retry
                      </Button>
                    </AlertDescription>
                  </Alert>
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
      <BulkBar selection={selection} />
    </div>
  );
}

/** The list, and from 1024px the detail pane beside it, both as tall as the screen allows. */
function Panes({
  wide,
  list,
  pane,
}: {
  wide: boolean;
  list: ReactNode;
  pane: ReactNode;
}) {
  return wide ? (
    <div className={cn("grid grid-cols-[26rem_minmax(0,1fr)] gap-4", PANES)}>
      <div className="flex min-h-0 flex-col gap-3">{list}</div>
      {pane}
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
      className="min-h-0 overflow-y-auto rounded-xl bg-card p-6 text-card-foreground"
    >
      <JobPosting
        key={id}
        id={id}
        titleLevel={2}
        actions={
          <Button asChild variant="secondary" size="sm">
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

/** No posting at all and no filter: what to do to get some. */
function EmptyList({ finder }: { finder: FindJobs }) {
  return (
    <section aria-labelledby="jobs-empty-title">
      <Empty className="gap-4 rounded-xl bg-card px-4 py-12 md:px-4 md:py-12">
        <EmptyHeader className="gap-1">
          <EmptyMedia
            variant="icon"
            className="mb-3 size-12 rounded-full bg-primary/10 text-primary"
          >
            <Briefcase aria-hidden="true" className="size-6" />
          </EmptyMedia>
          <EmptyTitle className="text-base font-semibold tracking-normal">
            <h2 id="jobs-empty-title">No postings yet.</h2>
          </EmptyTitle>
          <EmptyDescription className="text-sm">
            Tell the app where to look and it brings postings here.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="gap-2">
          <FindJobsButton finder={finder} />
          <AddPostingButton asLink />
        </EmptyContent>
      </Empty>
    </section>
  );
}
