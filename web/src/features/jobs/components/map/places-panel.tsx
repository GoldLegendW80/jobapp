import { ArrowLeft, MapPinOff } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { Progress } from "@/components/ui/progress";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { cn } from "@/lib/utils";

import { FitBadge, StatusBadge } from "@/components/shared/jobs/badges";
import { dominantStatus } from "@/features/jobs/helpers/map/geometry";
import {
  describePlace,
  plural,
  STATUS_BAR,
} from "@/features/jobs/helpers/map/format";
import type { MapSelection } from "../../hooks/use-map-selection";
import type { PlacesMap } from "@/features/jobs/api/map/places-api";

/**
 * The side of the map. With no place selected: every place as a button with its count (the
 * list alternative to the pins), then the locations the gazetteer cannot place, with their
 * counts. With a place selected: its postings, each a link to the detail page, and with
 * `selection` a checkbox each and one for the whole place, for the bulk bar (Apply, Like, status).
 */
export function PlacesPanel({
  data,
  selected,
  onSelect,
  from,
  selection,
  className,
}: {
  data: PlacesMap;
  selected: string | null;
  onSelect: (name: string | null) => void;
  /** Passed to the detail page, so its breadcrumb's Jobs returns to this map view. */
  from: string;
  selection?: MapSelection;
  className?: string;
}) {
  const place = data.places.find((p) => p.name === selected);
  if (place) {
    const listed = place.job_ids.filter((id) => data.jobs[id]);
    const ticked = listed.filter((id) => selection?.selected.has(id)).length;
    return (
      <aside
        aria-labelledby="place-title"
        className={cn(
          "flex min-w-0 flex-col rounded-xl bg-card text-card-foreground",
          className,
        )}
      >
        <div className="p-2">
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
        <div className="px-4">
          <h2 id="place-title" className="text-base font-medium">
            {place.name}
          </h2>
          <p className="text-sm text-muted-foreground">
            {describePlace(place)}
          </p>
          {selection && listed.length > 0 ? (
            <Field orientation="horizontal" className="mt-2 items-center gap-2">
              <Checkbox
                id="place-select-all"
                checked={
                  ticked === 0
                    ? false
                    : ticked === listed.length
                      ? true
                      : "indeterminate"
                }
                onCheckedChange={() => {
                  selection.setMany(listed, ticked < listed.length);
                }}
              />
              <FieldLabel htmlFor="place-select-all" className="font-normal">
                Select all of this place ({listed.length})
              </FieldLabel>
            </Field>
          ) : null}
        </div>
        <ul
          aria-label={`Postings in ${place.name}`}
          className="flex max-h-[32rem] min-h-0 flex-col overflow-auto p-2 lg:max-h-none"
        >
          {place.job_ids.map((id) => {
            const job = data.jobs[id];
            if (!job) return null;
            return (
              <li key={id} className="flex items-start gap-1">
                {selection ? (
                  <Checkbox
                    checked={selection.selected.has(id)}
                    aria-label={`Select ${job.title} at ${job.company}`}
                    // Its line with the title, as the Jobs list's cards.
                    className="mt-2.5 ml-2 shrink-0"
                    onCheckedChange={() => {
                      selection.toggle(id);
                    }}
                  />
                ) : null}
                <Item asChild size="sm" className="min-w-0 flex-1 px-2 py-2">
                  <Link to={`/jobs/${id}`} state={{ from }}>
                    <ItemContent className="min-w-0">
                      <ItemTitle className="w-auto break-words">
                        {job.title}
                      </ItemTitle>
                      <ItemDescription className="line-clamp-none text-xs break-words text-wrap">
                        {job.company}
                        {job.location ? `, ${job.location}` : ""}
                      </ItemDescription>
                      <span className="flex flex-wrap gap-1.5">
                        <StatusBadge status={job.status} />
                        <FitBadge fit={job.fit} />
                      </span>
                    </ItemContent>
                  </Link>
                </Item>
              </li>
            );
          })}
        </ul>
        {place.job_ids_truncated ? (
          <p className="px-4 pb-3 text-xs text-muted-foreground">
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
      className={cn(
        "flex min-w-0 flex-col rounded-xl bg-card text-card-foreground",
        className,
      )}
    >
      <div className="px-4 pt-4 pb-2">
        <h2 id="places-title" className="text-base font-medium">
          Places
        </h2>
        <p className="text-sm text-muted-foreground">
          {`${plural(data.places.length, "place")}, ${data.placed} of ${plural(data.total, "posting")} placed`}
        </p>
      </div>
      {data.places.length ? (
        <ul
          aria-label="Places, most postings first"
          className="flex max-h-[24rem] min-h-0 flex-col overflow-auto py-1 lg:max-h-none"
        >
          {data.places.map((p) => {
            const status = dominantStatus(p.counts_by_status);
            return (
              <li key={p.name}>
                <Button
                  type="button"
                  variant="ghost"
                  aria-label={`${p.name}: ${describePlace(p)}`}
                  onClick={() => {
                    onSelect(p.name);
                  }}
                  className="grid h-auto w-full grid-cols-[minmax(0,1fr)_4.5rem_3ch] justify-stretch gap-2.5 rounded-none px-4 py-1.5 text-left font-normal hover:text-foreground focus-visible:ring-inset dark:hover:bg-accent"
                >
                  <span className="truncate">{p.name}</span>
                  <Progress
                    aria-hidden="true"
                    value={(p.count / max) * 100}
                    className={cn(
                      "h-1 bg-muted",
                      status ? STATUS_BAR[status] : "*:bg-muted-foreground",
                    )}
                  />
                  <span className="text-right text-muted-foreground tabular-nums">
                    {p.count}
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-4 py-2 text-sm text-muted-foreground">
          No posting in this view names a known place.
        </p>
      )}
      {data.unplaced.length ? (
        <section
          aria-labelledby="unplaced-title"
          className="shrink-0 px-4 pt-3 pb-4"
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
                    !u.location && "text-muted-foreground",
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
