import {
  Ellipsis,
  ShieldCheck,
  ShieldOff,
  UserCheck,
  UserX,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import type { AdminUser } from "../../api/admin-api";
import {
  displayName,
  LAST_ADMIN_NOTE,
  type UserAction,
} from "@/features/admin/helpers/user-actions";

/** The actions on one account: its role, and whether it can sign in. */
export function UserActionsMenu({
  user,
  self,
  protectedAdmin,
  onAction,
}: {
  user: AdminUser;
  self: boolean;
  /** The last active admin: neither disabled nor made a member. */
  protectedAdmin: boolean;
  onAction: (user: AdminUser, action: UserAction) => void;
}) {
  const name = displayName(user);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="max-sm:w-full"
          aria-label={`Actions for ${name}`}
        >
          <Ellipsis aria-hidden="true" />
          <span className="sm:sr-only">Actions</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-64 max-w-[calc(100vw-2rem)]"
      >
        <DropdownMenuLabel className="truncate font-normal text-muted-foreground">
          {name}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {user.role === "member" ? (
          <DropdownMenuItem
            onSelect={() => {
              onAction(user, "promote");
            }}
          >
            <ShieldCheck aria-hidden="true" />
            Make admin
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem
            disabled={protectedAdmin}
            onSelect={() => {
              onAction(user, "demote");
            }}
          >
            <ShieldOff aria-hidden="true" />
            Make member
          </DropdownMenuItem>
        )}
        {user.is_active ? (
          // Your own account: another admin disables it, so you never lock yourself out here.
          self ? null : (
            <DropdownMenuItem
              variant="destructive"
              disabled={protectedAdmin}
              onSelect={() => {
                onAction(user, "disable");
              }}
            >
              <UserX aria-hidden="true" />
              Disable
            </DropdownMenuItem>
          )
        ) : (
          <DropdownMenuItem
            onSelect={() => {
              onAction(user, "enable");
            }}
          >
            <UserCheck aria-hidden="true" />
            Enable
          </DropdownMenuItem>
        )}
        {protectedAdmin ? (
          <>
            <DropdownMenuSeparator />
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              {LAST_ADMIN_NOTE}
            </p>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
