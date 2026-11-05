import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import {
  Briefcase,
  CircleAlert,
  RotateCw,
  SearchX,
  SlidersHorizontal,
} from "lucide-react";
import { useCallback, useId, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FinishSetupCard } from "@/features/onboarding/onboarding-gate";
import { describeError } from "@/lib/api";
import { useIsPhone } from "@/lib/use-media-query";
import { useTitle } from "@/lib/use-title";

import { jobListKey, listJobs, listQuery } from "./api";
import {
  FindJobsButton,
  FindJobsSetup,
  PastSearchesButton,
  SweepProgress,
  SweepSummaryDialog,
} from "./find-jobs";
import { AddPostingButton, BulkBar, PageMenu } from "./jobs-actions";
import { ScheduleButton } from "./schedule-dialog";
import { FilterBar } from "./filter-bar";
import {
  activeFilterCount,
  EMPTY_FILTERS,
  hasFilters,
  toggleFitRange,
  toggleOnlyStatus,
  type JobFilters,
} from "./filters";
import { FitHistogram } from "./fit-histogram";
import { JobsViewTabs } from "./map/view-tabs";
import { JobsList, JobsListSkeleton } from "./jobs-list";
import { KpiTiles } from "./kpi-tiles";
import { plural, useSelection } from "./selection";
import { useFindJobs, type FindJobs } from "./use-find-jobs";
import { useSweepRunner } from "./use-sweep-runner";
import { useJobFilters } from "./use-job-filters";

const EMPTY_BUCKETS = Array.from({ length: 10 }, (_, i) => ({
  low: i * 10,
  high: i === 9 ? 100 : i * 10 + 9,
  count: 0,
}));

/**
 * /jobs, the home page: counts per status, the fit histogram, the filters (all in the URL) and
 * the list of postings, loaded page by page as it scrolls. Rows can be selected (Shift for a
 * range, the header box for the whole view) for the bulk bar; the header finds jobs on the
 * person's list of places (live progress, then a summary), adds a posting by hand, and its menu
 * rescores. With no posting at all and no filter, the list says how to get postings and holds
 * Find jobs and Add a posting itself, without the tiles, the filters and the chart. On a phone
 * the filters fold behind one button, the chart is left out and the tiles scroll sideways, so
 * the first posting is near the top.
 */
export function JobsPage() {
  useTitle("Jobs");
  const { filters, setFilters, query } = useJobFilters();
  const apiQuery = listQuery(filters);
  const list = useInfiniteQuery({
    queryKey: jobListKey(apiQuery),
    queryFn: ({ pageParam, signal }) => listJobs(apiQuery, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });
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
  const phone = useIsPhone();
  // Nothing to count, filter or chart yet: no posting at all, and no filter that hides some.
  const empty = first !== undefined && first.total === 0 && !filtered;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 sm:gap-6">
      <JobsViewTabs current="list" query={query} />
      <FinishSetupCard />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          <p
            role="status"
            aria-live="polite"
            className="text-sm text-muted-foreground tabular-nums"
          >
            {first
              ? `${plural(first.total, "posting")}${filtered ? " match" : ""}`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* The empty list holds Find jobs and Add a posting itself. */}
          {empty ? null : <FindJobsButton finder={finder} />}
          <ScheduleButton />
          <PastSearchesButton runner={sweep} />
          {empty ? null : <AddPostingButton />}
          <PageMenu selected={selection.ids} />
        </div>
      </div>

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
          {phone ? (
            <FoldedFilters filters={filters} onChange={setFilters} />
          ) : (
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
              <FilterBar filters={filters} onChange={setFilters} />
              <FitHistogram
                buckets={first?.fit_histogram.buckets ?? EMPTY_BUCKETS}
                unscored={first?.fit_histogram.unscored ?? 0}
                fitMin={filters.fitMin}
                fitMax={filters.fitMax}
                loading={!first}
                onSelect={(low, high) => {
                  setFilters(toggleFitRange(filters, low, high));
                }}
              />
            </div>
          )}
        </>
      )}

      <div aria-busy={list.isFetching && !isFetchingNextPage}>
        {list.isPending ? (
          <JobsListSkeleton />
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
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-10 text-center">
              <SearchX
                aria-hidden="true"
                className="size-6 text-muted-foreground"
              />
              <p className="font-medium">No postings match these filters</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setFilters({ ...EMPTY_FILTERS, sort: filters.sort });
                }}
              >
                Clear filters
              </Button>
            </div>
          ) : (
            <EmptyList finder={finder} />
          )
        ) : (
          <>
            {list.isError ? (
              <Alert variant="destructive" className="mb-3">
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
              from={query ? `?${query}` : ""}
              selection={selection}
            />
            <div className="mt-2 flex sm:hidden">
              <Button variant="outline" size="sm" onClick={selection.toggleAll}>
                {selection.allState === "none"
                  ? "Select every posting in this view"
                  : "Clear the selection"}
              </Button>
            </div>
          </>
        )}
      </div>
      <BulkBar selection={selection} />
    </div>
  );
}

/** No posting at all and no filter: what to do to get some. */
function EmptyList({ finder }: { finder: FindJobs }) {
  return (
    <section
      aria-labelledby="jobs-empty-title"
      className="flex flex-col items-center gap-4 rounded-lg border border-dashed px-4 py-12 text-center"
    >
      <span
        aria-hidden="true"
        className="flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary"
      >
        <Briefcase className="size-6" />
      </span>
      <div className="flex max-w-sm flex-col gap-1">
        <h2 id="jobs-empty-title" className="font-semibold">
          No postings yet.
        </h2>
        <p className="text-sm text-muted-foreground">
          Tell the app where to look and it brings postings here.
        </p>
      </div>
      <div className="flex flex-col items-center gap-2">
        <FindJobsButton finder={finder} />
        <AddPostingButton asLink />
      </div>
    </section>
  );
}

/** A phone: the filters behind one button that counts those set. */
function FoldedFilters({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const active = activeFilterCount(filters);
  return (
    <div className="flex flex-col gap-3">
      <Button
        variant="outline"
        className="w-fit"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setOpen((old) => !old);
        }}
      >
        <SlidersHorizontal aria-hidden="true" />
        Filters
        {active ? (
          <>
            <span
              aria-hidden="true"
              className="inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground tabular-nums"
            >
              {active}
            </span>
            <span className="sr-only">, {active} set</span>
          </>
        ) : null}
      </Button>
      <div id={id} hidden={!open}>
        <FilterBar filters={filters} onChange={onChange} />
      </div>
    </div>
  );
}
