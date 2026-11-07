import { useState } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { Skeleton } from "@/components/ui/skeleton";
import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { FiltersToolbar } from "@/components/shared/jobs/filters/filters-toolbar";
import { JobsFrame } from "@/components/shared/jobs/jobs-frame";
import { useTitle } from "@/hooks/use-title";

import { listQuery } from "../api/jobs-api";
import { usePlaces } from "../api/map/use-places";
import { useJobs } from "../api/use-jobs";
import { useSweepRunner } from "../api/use-sweep-runner";
import { SweepProgress } from "@/components/shared/jobs/find-jobs";
import { HeaderActions } from "@/components/shared/jobs/jobs-actions/header-actions";
import { PlacesPanel } from "../components/map/places-panel";
import { WorldMap } from "../components/map/world-map";
import { NoPostingsYet } from "@/components/shared/jobs/no-postings-yet";
import { hasFilters, toSearchParams } from "../helpers/filters";
import { plural } from "../helpers/map/format";
import { useFindJobs } from "../hooks/use-find-jobs";
import { useJobFilters } from "../hooks/use-job-filters";
import { useMapSelection } from "../hooks/use-map-selection";

/**
 * /jobs/map, the Map view of Jobs: the postings of the current filters (the same URL query and
 * toolbar as the list) on a world map, a pin per city or country their location names, the places
 * as a list beside it, and the locations no place matches. A place's postings can be ticked, a
 * place at a time or all of it, and the list's bulk bar acts on them (Apply to the selected
 * postings, Like, status, note). The toolbar's counts come from the list's own query, so going
 * from one view to the other asks for them once.
 */
export function JobsMapPage() {
  useTitle("Jobs map");
  const { filters, setFilters, query } = useJobFilters();
  const counts = useJobs(listQuery(filters));
  const summary = counts.data?.pages[0];
  const apiQuery = toSearchParams(filters, { withSort: false }).toString();
  const places = usePlaces(apiQuery);
  const [selected, setSelected] = useState<string | null>(null);
  const selection = useMapSelection(apiQuery);
  const sweep = useSweepRunner();
  const finder = useFindJobs(sweep);
  const data = places.data;
  const filtered = hasFilters(filters);
  const from = `/map${query ? `?${query}` : ""}`;
  // Nothing to place yet: no posting at all, and no filter that hides some.
  const empty = data !== undefined && data.total === 0 && !filtered;

  return (
    <JobsFrame
      query={query}
      description={
        <span role="status" aria-live="polite" className="tabular-nums">
          {data
            ? `${data.placed} of ${plural(data.total, "posting")}${filtered ? " matching" : ""} on the map`
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
        <>
          <FiltersToolbar
            filters={filters}
            onChange={setFilters}
            summary={summary}
            unavailable={counts.isError && !summary}
          />
          {places.isError && !data ? (
            <LoadError query={places}>The map could not be loaded.</LoadError>
          ) : (
            <div
              aria-busy={places.isFetching}
              className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_24rem]"
            >
              <WorldMap
                places={data?.places ?? []}
                selected={selected}
                onSelect={setSelected}
              />
              {/* From lg the panel is as tall as the map beside it and scrolls inside. */}
              <div className="relative min-w-0">
                {data ? (
                  <PlacesPanel
                    data={data}
                    selected={selected}
                    onSelect={setSelected}
                    from={from}
                    selection={selection}
                    className="lg:absolute lg:inset-0"
                  />
                ) : (
                  <Skeleton
                    aria-hidden="true"
                    className="h-64 rounded-xl bg-card lg:absolute lg:inset-0 lg:h-auto"
                  />
                )}
              </div>
            </div>
          )}
        </>
      )}
      <BulkBar selection={selection} />
    </JobsFrame>
  );
}
