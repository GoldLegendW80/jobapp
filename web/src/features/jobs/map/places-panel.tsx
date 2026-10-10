import { ArrowLeft, MapPinOff } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { FitBadge, StatusBadge } from "../badges";
import { dominantStatus } from "./geometry";
import { describePlace, plural, STATUS_FILL } from "./format";
import type { PlacesMap } from "./places-api";

/**
 * The side of the map. With no place selected: every place as a button with its count (the
 * list alternative to the pins), then the locations the gazetteer cannot place, with their
 * counts. With a place selected: its postings, each a link to the detail page.
 */
export function PlacesPanel({
  data,
  selected,
  onSelect,
  from,
}: {
  data: PlacesMap;
  selected: string | null;
  onSelect: (name: string | null) => void;
  /** Passed to the detail page, so "Back to jobs" returns to this map view. */
  from: string;
}) {
  const place = data.places.find((p) => p.name === selected);
  if (place) {
    return (
      <aside
        aria-labelledby="place-title"
        className="flex min-w-0 flex-col rounded-lg border bg-card"
      >
        <div className="flex items-center gap-2 border-b p-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onSelect(null);
            }}
          >
            <ArrowLeft aria-hidden="true" />
            All places
          </Button>
        </div>
        <div className="px-4 pt-3">
          <h2 id="place-title" className="text-base font-semibold">
            {place.name}
          </h2>
          <p className="text-sm text-muted-foreground">
            {describePlace(place)}
          </p>
        </div>
        <ul
          aria-label={`Postings in ${place.name}`}
          className="flex max-h-[32rem] flex-col overflow-auto p-2"
        >
          {place.job_ids.map((id) => {
            const job = data.jobs[id];
            if (!job) return null;
            return (
              <li key={id}>
                <Link
                  to={`/jobs/${id}`}
                  state={{ from }}
                  className="flex flex-col gap-1 rounded-md px-2 py-2 hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  <span className="text-sm font-medium break-words">
                    {job.title}
                  </span>
                  <span className="text-xs break-words text-muted-foreground">
                    {job.company}
                    {job.location ? ` · ${job.location}` : ""}
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    <StatusBadge status={job.status} />
                    <FitBadge fit={job.fit} />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
        {place.job_ids_truncated ? (
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            {`Showing the first ${place.job_ids.length} of ${place.count}. Narrow the filters to see the others.`}
          </p>
        ) : null}
      </aside>
    );
  }

  const max = Math.max(1, ...data.places.map((p) => p.count));
  return (
    <aside
      aria-labelledby="places-title"
      className="flex min-w-0 flex-col rounded-lg border bg-card"
    >
      <div className="border-b px-4 py-3">
        <h2 id="places-title" className="text-base font-semibold">
          Places
        </h2>
        <p className="text-sm text-muted-foreground">
          {`${plural(data.places.length, "place")}, ${data.placed} of ${plural(data.total, "posting")} placed`}
        </p>
      </div>
      {data.places.length ? (
        <ul
          aria-label="Places, most postings first"
          className="flex max-h-[24rem] flex-col overflow-auto py-1"
        >
          {data.places.map((p) => {
            const status = dominantStatus(p.counts_by_status);
            return (
              <li key={p.name}>
                <button
                  type="button"
                  aria-label={`${p.name}: ${describePlace(p)}`}
                  onClick={() => {
                    onSelect(p.name);
                  }}
                  className="grid w-full grid-cols-[minmax(0,1fr)_4.5rem_3ch] items-center gap-2.5 px-4 py-1.5 text-left text-sm hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
                >
                  <span className="truncate">{p.name}</span>
                  <span
                    aria-hidden="true"
                    className="h-1 overflow-hidden rounded-full bg-muted"
                  >
                    <span
                      className={cn(
                        "block h-full rounded-full",
                        status ? STATUS_FILL[status] : "bg-muted-foreground",
                      )}
                      style={{ width: `${(p.count / max) * 100}%` }}
                    />
                  </span>
                  <span className="text-right text-muted-foreground tabular-nums">
                    {p.count}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-4 py-3 text-sm text-muted-foreground">
          No posting in this view names a known place.
        </p>
      )}
      {data.unplaced.length ? (
        <section
          aria-labelledby="unplaced-title"
          className="border-t px-4 py-3"
        >
          <h3
            id="unplaced-title"
            className="flex items-center gap-1.5 text-sm font-medium"
          >
            <MapPinOff
              aria-hidden="true"
              className="size-4 text-muted-foreground"
            />
            Unplaced
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {`${plural(data.unplaced_total, "posting")} without a place on the map. Their locations name no city or country the map knows.`}
          </p>
          {data.unplaced_locations > data.unplaced.length ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {`The ${data.unplaced.length} most frequent of ${data.unplaced_locations} locations.`}
            </p>
          ) : null}
          <ul
            aria-label="Unplaced locations"
            className="mt-2 flex max-h-40 flex-col gap-0.5 overflow-auto text-sm"
          >
            {data.unplaced.map((u) => (
              <li
                key={u.location}
                className="flex items-baseline justify-between gap-3"
              >
                <span
                  className={cn(
                    "min-w-0 break-words",
                    !u.location && "text-muted-foreground italic",
                  )}
                >
                  {u.location || "Location not stated"}
                </span>
                <span className="text-muted-foreground tabular-nums">
                  {u.count}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </aside>
  );
}
