import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { Briefcase, CircleAlert, RotateCw, SearchX } from "lucide-react";
import { useCallback } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { jobListKey, listJobs, listQuery } from "./api";
import { AddPostingButton, BulkBar, PageMenu } from "./jobs-actions";
import { FilterBar } from "./filter-bar";
import {
  EMPTY_FILTERS,
  hasFilters,
  toggleFitRange,
  toggleOnlyStatus,
} from "./filters";
import { FitHistogram } from "./fit-histogram";
import { JobsViewTabs } from "./map/view-tabs";
import { JobsList, JobsListSkeleton } from "./jobs-list";
import { KpiTiles } from "./kpi-tiles";
import { useSelection } from "./selection";
import { useJobFilters } from "./use-job-filters";

const EMPTY_BUCKETS = Array.from({ length: 10 }, (_, i) => ({
  low: i * 10,
  high: i === 9 ? 100 : i * 10 + 9,
  count: 0,
}));

/**
 * /jobs, the home page: counts per status, the fit histogram, the filters (all in the URL) and
 * the list of postings, loaded page by page as it scrolls. Rows can be selected (Shift for a
 * range, the header box for the whole view) for the bulk bar; the header adds a posting by
 * hand, and its menu rescores.
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

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <JobsViewTabs current="list" query={query} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
          <p
            role="status"
            aria-live="polite"
            className="text-sm text-muted-foreground tabular-nums"
          >
            {first
              ? `${first.total} ${first.total === 1 ? "posting" : "postings"}${filtered ? " match" : ""}`
              : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AddPostingButton />
          <PageMenu selected={selection.ids} />
        </div>
      </div>

      <KpiTiles
        counts={first?.counts_by_status}
        selected={filters.statuses}
        loading={!first}
        onSelect={(status) => {
          setFilters(toggleOnlyStatus(filters, status));
        }}
      />

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
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-10 text-center">
              <Briefcase
                aria-hidden="true"
                className="size-6 text-muted-foreground"
              />
              <p className="font-medium">No postings yet</p>
              <p className="max-w-sm text-sm text-muted-foreground">
                Postings you import or find appear here.
              </p>
            </div>
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
