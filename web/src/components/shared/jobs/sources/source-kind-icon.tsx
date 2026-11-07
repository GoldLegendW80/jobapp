import { Building2, Globe, Rss } from "lucide-react";

import { cn } from "@/lib/utils";

import type { RowKind } from "@/features/jobs/helpers/sources";

const ICONS: Record<RowKind, typeof Globe> = {
  website: Globe,
  feed: Rss,
  board: Building2,
};

/** The sign of a kind of address (a website, a job feed, a company job board) on its tile.
 * `className` sizes and rounds the tile. */
export function SourceKindIcon({
  kind,
  className,
}: {
  kind: RowKind;
  className?: string;
}) {
  const Icon = ICONS[kind];
  return (
    <span
      className={cn(
        "flex items-center justify-center bg-secondary text-secondary-foreground",
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-4" />
    </span>
  );
}
