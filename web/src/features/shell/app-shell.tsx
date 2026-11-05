import type { CSSProperties } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { useMe } from "@/features/auth/auth-context";
import { HealthDot } from "@/features/health/health-dot";
import { useIsPhone } from "@/lib/use-media-query";
import { APP_NAME } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import { BottomNav, TabCounts } from "./bottom-nav";
import { MAIN_TABS } from "./main-tabs";
import { UserMenu } from "./user-menu";

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex shrink-0 items-center rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
    isActive && "bg-accent text-accent-foreground",
  );

/** The height the phone's bottom tab bar takes, safe area included: the page keeps clear of it,
 * and a bar that sticks to the bottom of the page (the bulk bar, a save bar) sits above it. */
const BOTTOM_BAR = "calc(3.5rem + 1px + env(safe-area-inset-bottom))";

/**
 * The signed in frame: header with the app name, navigation and the user menu. Wide screens keep
 * the main navigation in the header; a phone (below `sm`) has a bottom tab bar instead, and the
 * header keeps the app name and the user menu, which then holds Admin too. Activity (the
 * assistant's runs) is in the user menu at every width.
 */
export function AppShell() {
  const { data: user } = useMe();
  const phone = useIsPhone();
  // RequireAuth renders this only with a user; the check keeps the types honest.
  if (!user) return null;
  const admin = user.role === "admin";
  return (
    <div
      className={cn("flex min-h-dvh flex-col", phone && "pb-(--bottom-bar)")}
      style={
        phone ? ({ "--bottom-bar": BOTTOM_BAR } as CSSProperties) : undefined
      }
    >
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link
            to="/jobs"
            className="rounded-md px-1 text-lg font-semibold tracking-tight"
          >
            {APP_NAME}
          </Link>
          {phone ? null : (
            <nav aria-label="Main" className="flex min-w-0 items-center gap-1">
              {MAIN_TABS.map((tab) => (
                <NavLink key={tab.to} to={tab.to} className={navLink}>
                  {tab.label}
                  {tab.counts ? <TabCounts /> : null}
                </NavLink>
              ))}
              {admin ? (
                <NavLink to="/admin/users" className={navLink}>
                  Admin
                </NavLink>
              ) : null}
            </nav>
          )}
          <div className="ml-auto">
            <UserMenu user={user} adminLink={phone && admin} />
          </div>
        </div>
      </header>
      {/* Right after the header in the page's order, as the header's navigation is on a wide
          screen: the skip link passes it, and Tab reaches it before the page. */}
      {phone ? <BottomNav /> : null}
      <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 sm:py-10">
        <Outlet />
      </main>
      <footer className="border-t">
        <div className="mx-auto flex h-10 max-w-6xl items-center justify-between gap-4 px-4">
          <Link
            to="/privacy"
            className="rounded-md text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Privacy and contact
          </Link>
          <HealthDot />
        </div>
      </footer>
    </div>
  );
}
