import {
  Activity,
  LogOut,
  Settings,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { Link, NavLink } from "react-router";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { User } from "@/features/auth/api/auth-api";
import { useAuth } from "@/features/auth/helpers/auth-context";

import { initials } from "./initials";

/**
 * The account menu at the right of the header: Account, Settings, Activity (the assistant's
 * runs, at every width), Admin for an admin on a phone (a wide screen has it in the header), and
 * Sign out.
 */
export function UserMenu({
  user,
  adminLink = false,
}: {
  user: User;
  /** Show Admin here: the header shows no navigation (a phone) and the user is an admin. */
  adminLink?: boolean;
}) {
  const { signOut, signingOut } = useAuth();
  const label = user.name || user.email;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="surface"
          className="h-11 max-w-60 gap-2 pr-1.5 pl-1.5 sm:pr-4"
          aria-label={`Account menu, ${label}`}
        >
          <Avatar aria-hidden="true">
            <AvatarFallback className="text-xs font-medium">
              {initials(user)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden truncate sm:inline">{label}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-64 max-w-[calc(100vw-2rem)]"
      >
        <DropdownMenuLabel className="flex flex-col gap-0.5 font-normal">
          <span className="truncate font-medium">{user.name}</span>
          <span className="truncate text-xs text-muted-foreground">
            {user.email}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/account">
            <UserRound aria-hidden="true" />
            Account
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/settings/search">
            <Settings aria-hidden="true" />
            Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <NavLink to="/runs">
            <Activity aria-hidden="true" />
            Activity
          </NavLink>
        </DropdownMenuItem>
        {adminLink ? (
          <DropdownMenuItem asChild>
            <NavLink to="/admin/users">
              <ShieldCheck aria-hidden="true" />
              Admin
            </NavLink>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={signingOut}
          onSelect={() => {
            signOut();
          }}
        >
          <LogOut aria-hidden="true" />
          {signingOut ? "Signing out" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
