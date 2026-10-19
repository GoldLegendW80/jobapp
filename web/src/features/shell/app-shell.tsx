import { Link, NavLink, Outlet } from "react-router";

import { QueueCount } from "@/features/apply/queue-count";
import { useMe } from "@/features/auth/auth-context";
import { HealthDot } from "@/features/health/health-dot";
import { APP_NAME } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import { UserMenu } from "./user-menu";

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "shrink-0 rounded-md px-2 py-2 text-sm font-medium text-muted-foreground sm:px-3 transition-colors hover:bg-accent hover:text-accent-foreground",
    isActive && "bg-accent text-accent-foreground",
  );

/** The signed in frame: header with the app name, navigation and the user menu. */
export function AppShell() {
  const { data: user } = useMe();
  // RequireAuth renders this only with a user; the check keeps the types honest.
  if (!user) return null;
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-2 px-4 sm:gap-4">
          <Link
            to="/jobs"
            className="rounded-md px-1 text-lg font-semibold tracking-tight"
          >
            {APP_NAME}
          </Link>
          <nav
            aria-label="Main"
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto sm:gap-1"
          >
            <NavLink to="/jobs" className={navLink}>
              Jobs
            </NavLink>
            <NavLink to="/profile" className={navLink}>
              Profile
            </NavLink>
            <NavLink to="/applications" className={navLink}>
              Applications
              <QueueCount />
            </NavLink>
            <NavLink to="/runs" className={navLink}>
              Runs
            </NavLink>
            {user.role === "admin" ? (
              <NavLink to="/admin/users" className={navLink}>
                Admin
              </NavLink>
            ) : null}
          </nav>
          <div className="ml-auto">
            <UserMenu user={user} />
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 sm:py-10">
        <Outlet />
      </main>
      <footer className="border-t">
        <div className="mx-auto flex h-10 max-w-6xl items-center justify-end px-4">
          <HealthDot />
        </div>
      </footer>
    </div>
  );
}
