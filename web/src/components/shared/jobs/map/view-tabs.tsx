import { Link } from "react-router";

import { cn } from "@/lib/utils";

/**
 * List or Map: two views of the same filtered postings. Both links carry the current filter
 * query, so switching keeps the view's filters.
 */
export function JobsViewTabs({
  current,
  query,
}: {
  current: "list" | "map";
  query: string;
}) {
  const suffix = query ? `?${query}` : "";
  const tabs = [
    { id: "list", label: "List", to: `/jobs${suffix}` },
    { id: "map", label: "Map", to: `/jobs/map${suffix}` },
  ] as const;
  return (
    <nav aria-label="Jobs views" className="w-fit">
      <ul className="inline-flex items-center rounded-lg bg-muted p-1 text-sm">
        {tabs.map((tab) => (
          <li key={tab.id}>
            <Link
              to={tab.to}
              aria-current={current === tab.id ? "page" : undefined}
              className={cn(
                "inline-flex h-8 items-center rounded-md px-3 font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                current === tab.id &&
                  "bg-background text-foreground shadow-sm dark:bg-input/30",
              )}
            >
              {tab.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
