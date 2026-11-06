import type { ComponentProps, ReactNode } from "react";
import { Link, useLocation } from "react-router";

import { cn } from "@/lib/utils";

/** Sections side by side in one white pill; the current one is a black pill. */
export function PillNav({
  label,
  className,
  children,
}: {
  /** The navigation's accessible name, "Main" for the app's sections. */
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <nav
      aria-label={label}
      className={cn(
        "flex min-w-0 items-center gap-1 overflow-x-auto rounded-full bg-card p-1 [scrollbar-width:none]",
        className,
      )}
    >
      {children}
    </nav>
  );
}

/**
 * One section of the nav. It is current on its own path and below it, or anywhere under
 * `section` when the link opens one page of a wider area (Admin opens /admin/users); `end`
 * keeps it current on its own path only (Home at /).
 */
export function PillNavLink({
  to,
  section = to,
  end = false,
  className,
  ...props
}: Omit<ComponentProps<typeof Link>, "className" | "to"> & {
  to: string;
  section?: string;
  end?: boolean;
  className?: string;
}) {
  const { pathname } = useLocation();
  const current =
    pathname === section || (!end && pathname.startsWith(`${section}/`));
  return (
    <Link
      to={to}
      aria-current={current ? "page" : undefined}
      className={cn(
        "flex h-9 shrink-0 items-center gap-1.5 rounded-full px-4 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50",
        current &&
          "bg-primary text-primary-foreground hover:text-primary-foreground",
        className,
      )}
      {...props}
    />
  );
}
