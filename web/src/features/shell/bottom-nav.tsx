import { NavLink } from "react-router";

import { NeedsYouDot } from "@/features/apply/needs-person";
import { QueueCount } from "@/features/apply/queue-count";
import { cn } from "@/lib/utils";

import { MAIN_TABS } from "./main-tabs";

/** The Applications counts, after its label so a screen reader reads them in its name. */
export function TabCounts() {
  return (
    <>
      <QueueCount />
      <NeedsYouDot />
    </>
  );
}

/**
 * The phone's tab bar (below `sm`): the main navigation with an icon and a label each, fixed at
 * the bottom of the screen above the safe area. The shell keeps the page clear of it.
 */
export function BottomNav() {
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="mx-auto grid h-14 max-w-md grid-cols-4">
        {MAIN_TABS.map(({ to, label, icon: Icon, counts }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "group relative flex min-w-0 flex-col items-center justify-center gap-1 rounded-md text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                isActive && "text-foreground",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span
                  aria-hidden="true"
                  className={cn(
                    "flex h-7 w-12 items-center justify-center rounded-full transition-colors",
                    isActive && "bg-accent text-primary",
                  )}
                >
                  <Icon className="size-5" />
                </span>
                <span
                  className={cn(
                    "max-w-full truncate text-xs leading-none",
                    isActive ? "font-semibold" : "font-medium",
                  )}
                >
                  {label}
                </span>
                {counts ? (
                  // Over the icon's top right corner; its words stay after the label.
                  <span className="pointer-events-none absolute top-1 left-1/2 flex items-center">
                    <TabCounts />
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
