import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Facts about one thing (a posting, a document, a run): a grid of small labels over values. */
export function Facts({
  className,
  children,
  ...props
}: React.ComponentProps<"dl">) {
  return (
    <dl
      className={cn("grid grid-cols-2 gap-x-6 gap-y-4", className)}
      {...props}
    >
      {children}
    </dl>
  );
}

/** One fact: its label, then its value. `describedBy` ties a note to the value (the cost note). */
export function Fact({
  label,
  describedBy,
  className,
  children,
}: {
  label: ReactNode;
  describedBy?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        aria-describedby={describedBy}
        className="text-sm font-medium tabular-nums [overflow-wrap:anywhere]"
      >
        {children}
      </dd>
    </div>
  );
}
