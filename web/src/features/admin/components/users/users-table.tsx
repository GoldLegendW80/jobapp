import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

import type { AdminUser } from "../../api/admin-api";
import {
  displayName,
  type UserAction,
} from "@/features/admin/helpers/user-actions";
import { Moment } from "@/components/shared/admin/moment";
import { BrowsersControl } from "./browsers-control";
import { UserActionsMenu } from "./user-actions-menu";

/**
 * One page of accounts: name, role, status, last sign in, sessions, browsers at once (saved as
 * soon as it is picked) and the actions menu.
 */
export function UsersTable({
  items,
  now,
  meId,
  lastAdmin,
  busy,
  browsersMax,
  savingBrowsers,
  onBrowsers,
  onAction,
}: {
  items: AdminUser[];
  now: Date;
  meId: string | undefined;
  lastAdmin: boolean;
  busy: boolean;
  browsersMax: number;
  savingBrowsers: string | undefined;
  onBrowsers: (user: AdminUser, browsers: number) => void;
  onAction: (user: AdminUser, action: UserAction) => void;
}) {
  return (
    <Table busy={busy}>
      <TableCaption className="sr-only">Accounts, by name</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Last sign in</TableHead>
          <TableHead>Sessions</TableHead>
          <TableHead>Browsers at once</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((user) => {
          const self = user.id === meId;
          return (
            <TableRow
              key={user.id}
              data-disabled={!user.is_active || undefined}
            >
              <TableHead className={cn(!user.is_active && "opacity-70")}>
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1 break-words">
                  {displayName(user)}
                  {self ? (
                    <Badge variant="secondary" aria-label="(you)">
                      You
                    </Badge>
                  ) : null}
                </span>
                <span className="block text-xs font-normal break-all text-muted-foreground">
                  {user.email}
                </span>
              </TableHead>
              <TableCell label="Role">
                <Badge
                  variant="outline"
                  className={
                    user.role === "admin"
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : "text-muted-foreground"
                  }
                >
                  {user.role === "admin" ? "Admin" : "Member"}
                </Badge>
              </TableCell>
              <TableCell label="Status">
                <AccountStatus active={user.is_active} />
              </TableCell>
              <TableCell label="Last sign in">
                {user.last_login_at ? (
                  <Moment at={user.last_login_at} now={now} />
                ) : (
                  <span className="text-muted-foreground">Never</span>
                )}
              </TableCell>
              <TableCell label="Sessions">
                <span className="tabular-nums">{user.sessions}</span>
              </TableCell>
              <TableCell label="Browsers at once">
                <BrowsersControl
                  user={user}
                  max={browsersMax}
                  saving={savingBrowsers === user.id}
                  onChange={(browsers) => {
                    onBrowsers(user, browsers);
                  }}
                />
              </TableCell>
              <TableCell className="text-right max-sm:pt-2 max-sm:text-left">
                <UserActionsMenu
                  user={user}
                  self={self}
                  protectedAdmin={
                    lastAdmin && user.is_active && user.role === "admin"
                  }
                  onAction={onAction}
                />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function AccountStatus({ active }: { active: boolean }) {
  return active ? (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="size-2 rounded-full bg-success" />
      Active
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
      <span
        aria-hidden="true"
        className="size-2 rounded-full border border-muted-foreground"
      />
      Disabled
    </span>
  );
}
