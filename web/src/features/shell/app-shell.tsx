import { Link, NavLink, Outlet } from "react-router";

import { useMe } from "@/features/auth/auth-context";
import { APP_NAME } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import { UserMenu } from "./user-menu";

const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
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
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4 sm:gap-4">
          <Link
            to="/"
            className="rounded-md px-1 text-lg font-semibold tracking-tight"
          >
            {APP_NAME}
          </Link>
          <nav aria-label="Main" className="flex items-center gap-1">
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
      <main id="main" tabIndex={-1} className="flex-1 px-4 py-8 sm:py-12">
        <Outlet />
      </main>
    </div>
  );
}
