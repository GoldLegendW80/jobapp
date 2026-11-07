import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The frame of every signed-in page, in this order: PageHeader, PageTabs when the area has
 * several views, then PageBody. It spans the shell's full width and sets the rhythm between them.
 */
export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-360 flex-col gap-6 sm:gap-8">
      {children}
    </div>
  );
}

/**
 * The content of a page: its sections in one column, and with `aside` a side rail on the right
 * for what accompanies them (status, facts, help). `lead` opens the rail and, on a narrow body,
 * comes before the main column: it is for what the person must reach first (Apply on a posting).
 * The split follows the width the body gets, not the window's, so the same composition stacks in
 * a narrow pane.
 */
export function PageBody({
  lead,
  aside,
  className,
  children,
}: {
  lead?: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  if (!aside && !lead)
    return (
      <div className={cn("flex min-w-0 flex-col gap-8", className)}>
        {children}
      </div>
    );
  return (
    <div className="@container">
      <div className="grid items-start gap-8 @4xl:grid-cols-[minmax(0,1fr)_20rem] @4xl:grid-rows-[auto_1fr] @4xl:gap-y-6 @6xl:grid-cols-[minmax(0,1fr)_24rem]">
        {lead ? (
          <div className="flex min-w-0 flex-col gap-6 @4xl:col-start-2 @4xl:row-start-1">
            {lead}
          </div>
        ) : null}
        <div
          className={cn(
            "flex min-w-0 flex-col gap-8 @4xl:col-start-1 @4xl:row-span-2 @4xl:row-start-1",
            className,
          )}
        >
          {children}
        </div>
        {aside ? (
          <aside
            className={cn(
              "flex min-w-0 flex-col gap-6 @4xl:col-start-2",
              lead ? "@4xl:row-start-2" : "@4xl:row-start-1",
            )}
          >
            {aside}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
