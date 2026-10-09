import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CircleAlert, RotateCw } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { FilterBar } from "../filter-bar";
import { hasFilters, toSearchParams } from "../filters";
import { useJobFilters } from "../use-job-filters";
import { plural } from "./format";
import { getPlaces, placesKey } from "./places-api";
import { PlacesPanel } from "./places-panel";
import { JobsViewTabs } from "./view-tabs";
import { WorldMap } from "./world-map";

/**
 * /jobs/map: the postings of the current filters (the same URL query as the list) on a world
 * map, a pin per city or country their location names, the places as a list beside it, and the
 * locations no place matches. A port of the Map view of engine/dashboard.html.
 */
export function JobsMapPage() {
  useTitle("Jobs map");
  const { filters, setFilters, query } = useJobFilters();
  const apiQuery = toSearchParams(filters, { withSort: false }).toString();
  const places = useQuery({
    queryKey: placesKey(apiQuery),
    queryFn: ({ signal }) => getPlaces(apiQuery, signal),
    placeholderData: keepPreviousData,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const data = places.data;
  const from = `/map${query ? `?${query}` : ""}`;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <JobsViewTabs current="map" query={query} />
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Jobs map</h1>
        <p
          role="status"
          aria-live="polite"
          className="text-sm text-muted-foreground tabular-nums"
        >
          {data
            ? `${data.placed} of ${plural(data.total, "posting")}${hasFilters(filters) ? " matching" : ""} on the map`
            : ""}
        </p>
      </div>

      <FilterBar filters={filters} onChange={setFilters} />

      {places.isError && !data ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>The map could not be loaded. {describeError(places.error)}</p>
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => void places.refetch()}
              disabled={places.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <div
          aria-busy={places.isFetching}
          className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]"
        >
          <WorldMap
            places={data?.places ?? []}
            selected={selected}
            onSelect={setSelected}
          />
          {data ? (
            <PlacesPanel
              data={data}
              selected={selected}
              onSelect={setSelected}
              from={from}
            />
          ) : (
            <div
              aria-hidden="true"
              className="h-64 animate-pulse rounded-lg border bg-muted/50"
            />
          )}
        </div>
      )}
    </div>
  );
}
