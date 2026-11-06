import { RotateCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { SectionVersions } from "@/features/profile/api/use-section-versions";
import { absoluteTime, relativeTime } from "@/features/profile/helpers/when";

/** The left pane of the History drawer: the saves of a section, newest first, to choose from. */
export function VersionList({
  label,
  versions,
  selected,
  onSelect,
}: {
  label: string;
  versions: SectionVersions;
  selected: number | undefined;
  onSelect: (revision: number) => void;
}) {
  const { query, items, newest, moreError } = versions;
  return (
    <nav
      aria-label={`Versions of ${label.toLowerCase()}`}
      className={cn(
        "min-h-0 overflow-y-auto border-b p-2 md:border-r md:border-b-0",
        selected !== undefined && "hidden md:block",
      )}
    >
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 p-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" />
          Loading versions
        </p>
      ) : query.isError ? (
        <div className="flex flex-col gap-2 p-2">
          <p role="alert" className="text-sm text-destructive">
            The versions could not be loaded. {describeError(query.error)}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : items.length === 0 ? (
        <p className="p-2 text-sm text-muted-foreground">
          No saved version of {label.toLowerCase()} yet. Each save adds one
          here.
        </p>
      ) : (
        <>
          <ol className="flex flex-col gap-1">
            {items.map((item) => (
              <li key={item.revision}>
                <Button
                  type="button"
                  variant="ghost"
                  aria-current={selected === item.revision ? "true" : undefined}
                  aria-label={`Revision ${String(item.revision)}${item.revision === newest ? ", current" : ""}, saved ${relativeTime(item.created_at)}, ${absoluteTime(item.created_at)}`}
                  onClick={() => {
                    onSelect(item.revision);
                  }}
                  className={cn(
                    "h-auto w-full flex-col items-start gap-0.5 px-3 py-2 text-left font-normal whitespace-normal",
                    selected === item.revision && "bg-accent",
                  )}
                >
                  <span className="flex items-center gap-2 font-medium">
                    Revision {item.revision}
                    {item.revision === newest ? (
                      <Badge variant="secondary">Current</Badge>
                    ) : null}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    <time dateTime={item.created_at}>
                      {relativeTime(item.created_at)}
                    </time>
                    <span aria-hidden="true">, </span>
                    <span className="sr-only">, </span>
                    {absoluteTime(item.created_at)}
                  </span>
                </Button>
              </li>
            ))}
          </ol>
          {query.hasNextPage ? (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2 w-full"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? <Spinner aria-hidden="true" /> : null}
              {query.isFetchingNextPage ? "Loading" : "Load more"}
            </Button>
          ) : null}
          {moreError ? (
            <p role="alert" className="mt-2 px-2 text-sm text-destructive">
              More versions could not be loaded. {describeError(moreError)}
            </p>
          ) : null}
        </>
      )}
    </nav>
  );
}
