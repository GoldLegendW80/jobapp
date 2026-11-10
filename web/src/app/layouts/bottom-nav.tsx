import { Link, useLocation } from "react-router";

import {
  NavigationMenu,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
} from "@/components/ui/navigation-menu";

import { NeedsYouDot } from "@/components/shared/apply/needs-person";
import { ActivityCount } from "@/features/agents/components/activity-count";
import { QueueCount } from "@/features/apply/components/queue-count";
import { cn } from "@/lib/utils";

import { MAIN_TABS, type TabCount } from "./main-tabs";

/** A tab's counts, after its label so a screen reader reads them in its name. */
export function TabCounts({ of }: { of: TabCount }) {
  if (of === "activity") return <ActivityCount />;
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
  const { pathname } = useLocation();
  return (
    <NavigationMenu
      aria-label="Main"
      viewport={false}
      className="fixed inset-x-0 bottom-0 z-40 max-w-none border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <NavigationMenuList className="mx-auto grid h-14 w-full max-w-lg grid-cols-5 gap-0">
        {MAIN_TABS.map(({ to, label, icon: Icon, counts }) => {
          const isActive = pathname === to || pathname.startsWith(`${to}/`);
          return (
            <NavigationMenuItem key={to} className="h-full">
              <NavigationMenuLink
                asChild
                active={isActive}
                className="group relative h-full min-w-0 flex-col justify-center gap-1 rounded-md px-0 data-active:bg-transparent data-active:text-foreground data-active:hover:text-foreground"
              >
                <Link to={to}>
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
                      <TabCounts of={counts} />
                    </span>
                  ) : null}
                </Link>
              </NavigationMenuLink>
            </NavigationMenuItem>
          );
        })}
      </NavigationMenuList>
    </NavigationMenu>
  );
}
