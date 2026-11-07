import type { ComponentProps, ReactNode } from "react";
import { Link, useLocation } from "react-router";

import {
  NavigationMenu,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
} from "@/components/ui/navigation-menu";
import { cn } from "@/lib/utils";

/** The app's sections side by side in one white pill (shadcn NavigationMenu); the current one is black. */
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
    <NavigationMenu
      aria-label={label}
      viewport={false}
      className={cn("min-w-0", className)}
    >
      <NavigationMenuList className="min-w-0 justify-start overflow-x-auto rounded-full bg-card p-1 [scrollbar-width:none]">
        {children}
      </NavigationMenuList>
    </NavigationMenu>
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
    <NavigationMenuItem>
      <NavigationMenuLink asChild active={current} className={className}>
        <Link to={to} {...props} />
      </NavigationMenuLink>
    </NavigationMenuItem>
  );
}
