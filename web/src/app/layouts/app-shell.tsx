import { Suspense, type CSSProperties } from "react";
import { Link, Outlet } from "react-router";

import { Brand } from "@/components/blocks/brand";
import { PageLoading } from "@/components/blocks/page-status";
import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";
import { useMe } from "@/features/auth/api/use-me";
import { HealthDot } from "@/features/health/components/health-dot";
import { useIsPhone } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

import { BottomNav, TabCounts } from "./bottom-nav";
import { MAIN_TABS } from "./main-tabs";
import { UserMenu } from "./user-menu";

/** The height the phone's bottom tab bar takes, safe area included: the page keeps clear of it,
 * and a bar that sticks to the bottom of the page (the bulk bar, a save bar) sits above it. */
const BOTTOM_BAR = "calc(3.5rem + 1px + env(safe-area-inset-bottom))";

/**
 * The signed in frame: header with the app name, navigation and the user menu. Wide screens keep
 * the main navigation in the header; a phone (below `sm`) has a bottom tab bar instead, and the
 * header keeps the app name and the user menu, which then holds Admin too.
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
      <header className="sticky top-0 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-16 max-w-360 items-center gap-4 px-4 sm:grid sm:px-6 lg:px-8 sm:grid-cols-[1fr_auto_1fr]">
          <Brand to="/jobs" />
          {phone ? null : (
            <PillNav label="Main">
              {MAIN_TABS.map((tab) => (
                <PillNavLink key={tab.to} to={tab.to}>
                  {tab.label}
                  {tab.counts ? <TabCounts /> : null}
                </PillNavLink>
              ))}
              {admin ? (
                <PillNavLink to="/admin/users" section="/admin">
                  Admin
                </PillNavLink>
              ) : null}
            </PillNav>
          )}
          <div className="ml-auto">
            <UserMenu user={user} adminLink={phone && admin} />
          </div>
        </div>
      </header>
      {/* Right after the header in the page's order, as the header's navigation is on a wide
          screen: the skip link passes it, and Tab reaches it before the page. */}
      {phone ? <BottomNav /> : null}
      <main
        id="main"
        tabIndex={-1}
        className="flex-1 px-4 py-6 sm:px-6 sm:py-10 lg:px-8"
      >
        {/* The header and the navigation stay while a page's code loads. */}
        <Suspense fallback={<PageLoading />}>
          <Outlet />
        </Suspense>
      </main>
      <footer>
        <div className="mx-auto flex h-10 max-w-360 items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
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
