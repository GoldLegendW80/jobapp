import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * One part of a page: an h2, a line on what it holds, its own actions, then its content on one
 * surface (a Card, a table, a list of item cards; never a card in a card). `side` puts the
 * heading in a column beside the content, for settings: what a group does on the left, its
 * controls on the right.
 */
export function Section({
  title,
  description,
  actions,
  layout = "stacked",
  className,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  layout?: "stacked" | "side";
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  const side = layout === "side";
  return (
    <section
      aria-labelledby={id}
      className={cn(
        "flex min-w-0 flex-col gap-4",
        side &&
          "lg:grid lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] lg:gap-x-12",
        className,
      )}
    >
      <div
        className={cn(
          "flex flex-wrap items-end justify-between gap-x-4 gap-y-2",
          side && "lg:flex-col lg:items-start lg:justify-start",
        )}
      >
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id={id} className="text-lg font-medium tracking-tight">
            {title}
          </h2>
          {description ? (
            <p className="max-w-prose text-sm text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-3">{children}</div>
    </section>
  );
}
