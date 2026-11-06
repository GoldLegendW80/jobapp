import { CircleAlert, RotateCw } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { describeError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { FilterBar } from "../components/filters/filter-bar";
import { hasFilters, toSearchParams } from "../helpers/filters";
import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { useJobFilters } from "../hooks/use-job-filters";
import { plural } from "../helpers/map/format";
import { useMapSelection } from "../hooks/use-map-selection";
import { usePlaces } from "../api/map/use-places";
import { PlacesPanel } from "../components/map/places-panel";
import { JobsViewTabs } from "@/components/shared/jobs/map/view-tabs";
import { WorldMap } from "../components/map/world-map";

/**
 * /jobs/map: the postings of the current filters (the same URL query as the list) on a world
 * map, a pin per city or country their location names, the places as a list beside it, and the
 * locations no place matches. A port of the Map view of engine/dashboard.html. A place's
 * postings can be ticked, a place at a time or all of it, and the Jobs list's bulk bar acts on
 * them (Apply to the selected postings, Like, status, note).
 */
export function JobsMapPage() {
  useTitle("Jobs map");
  const { filters, setFilters, query } = useJobFilters();
  const apiQuery = toSearchParams(filters, { withSort: false }).toString();
  const places = usePlaces(apiQuery);
  const [selected, setSelected] = useState<string | null>(null);
  const selection = useMapSelection(apiQuery);
  const data = places.data;
  const from = `/map${query ? `?${query}` : ""}`;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <JobsViewTabs current="map" query={query} />
      <PageHeader
        title="Jobs map"
        description={
          <span role="status" aria-live="polite" className="tabular-nums">
            {data
              ? `${data.placed} of ${plural(data.total, "posting")}${hasFilters(filters) ? " matching" : ""} on the map`
              : ""}
          </span>
        }
      />

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
              selection={selection}
            />
          ) : (
            <Skeleton
              aria-hidden="true"
              className="h-64 rounded-lg border bg-muted/50"
            />
          )}
        </div>
      )}
      <BulkBar selection={selection} />
    </div>
  );
}
