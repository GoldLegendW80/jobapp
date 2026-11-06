import type { ReactNode, Ref } from "react";

import { cn } from "@/lib/utils";

/**
 * The top of every page: its h1, one line on what the page is for, and the page's own actions
 * (the primary one first). The title is the page's only h1; `titleRef` lets a page move focus to
 * it, for instance after the item that had focus is gone.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
  titleRef,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  titleRef?: Ref<HTMLHeadingElement>;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-end justify-between gap-4",
        className,
      )}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h1
          ref={titleRef}
          tabIndex={titleRef ? -1 : undefined}
          className="text-3xl font-medium tracking-tight outline-none [overflow-wrap:anywhere]"
        >
          {title}
        </h1>
        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
