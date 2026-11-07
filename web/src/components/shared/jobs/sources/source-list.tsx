import { ListPlus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Empty, EmptyDescription, EmptyMedia } from "@/components/ui/empty";
import type { Search } from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";

import type { Sources } from "@/features/jobs/api/use-sources";
import {
  PLATFORM_NAMES,
  rowsOf,
  sourceCount,
  type SourceRow,
} from "@/features/jobs/helpers/sources";
import { BLOCK, BOX } from "../setup-grid";
import { SourceLine } from "./source-line";

/** "Your list": how many places a search reads, a line per address, what saving takes back, and
 * the last change said to a screen reader. */
export function SourceList({
  id,
  sources,
  reviewed,
  disabled,
  said,
  onRemove,
  onResume,
}: {
  id: string;
  sources: Sources;
  reviewed: Search;
  disabled: boolean;
  /** The last change, for a screen reader. */
  said: string;
  onRemove: (row: SourceRow) => void;
  onResume: (row: SourceRow) => void;
}) {
  const rows = rowsOf(sources.draft);
  const count = sourceCount(sources.draft);
  const withdrawn = sources.withdrawn(reviewed);
  return (
    <div className={BLOCK}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium" id={`${id}-list`}>
          Your list
        </p>
        <Badge variant={count ? "secondary" : "outline"}>
          {count === 1 ? "1 place" : `${String(count)} places`}
        </Badge>
      </div>
      {rows.length === 0 ? (
        // On the grid: the sign in the gutter, the words at the column's edge.
        <Empty
          className={cn(
            BOX,
            "items-start gap-0 rounded-xl border-dashed py-3 text-left text-muted-foreground md:py-3 md:pr-3 md:pl-[calc(0.5rem-1px)]",
          )}
        >
          <EmptyMedia aria-hidden="true" className="mb-0 h-5 w-7">
            <ListPlus className="size-4" />
          </EmptyMedia>
          <EmptyDescription className="text-sm text-wrap">
            Nothing yet. Add an address above or pick an example, and the app
            searches every place on your list.
          </EmptyDescription>
        </Empty>
      ) : (
        <ul aria-labelledby={`${id}-list`} className="flex flex-col gap-2">
          {rows.map((row) => (
            <SourceLine
              key={row.key}
              row={row}
              sources={sources}
              reviewed={reviewed}
              disabled={disabled}
              onRemove={() => {
                onRemove(row);
              }}
              onResume={() => {
                onResume(row);
              }}
            />
          ))}
        </ul>
      )}
      {withdrawn.length ? (
        <p className="text-xs text-muted-foreground">
          Saving takes back your permission to search{" "}
          {withdrawn.map((platform) => PLATFORM_NAMES[platform]).join(" and ")}.
        </p>
      ) : null}
      <p aria-live="polite" className="sr-only">
        {said}
      </p>
    </div>
  );
}
