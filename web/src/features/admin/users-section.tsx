import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Ellipsis,
  RotateCw,
  ShieldCheck,
  ShieldOff,
  UserCheck,
  UserX,
  Users,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { meQueryKey, useMe } from "@/features/auth/auth-context";
import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  USERS_PAGE_SIZE,
  invitesKey,
  listUsers,
  updateUser,
  usersKey,
  type AdminUser,
  type UserChange,
} from "./api";
import { message } from "./errors";
import { Moment } from "./moment";

type Action = "promote" | "demote" | "disable" | "enable";

interface Pending {
  user: AdminUser;
  action: Action;
  self: boolean;
}

const CHANGE: Record<Action, UserChange> = {
  promote: { role: "admin" },
  demote: { role: "member" },
  disable: { is_active: false },
  enable: { is_active: true },
};

export const LAST_ADMIN_NOTE =
  "The last active admin can be neither disabled nor made a member. Make someone else an admin first.";

const displayName = (user: AdminUser) => user.name || user.email;

/** Who can sign in: role, status, last sign in, signed in browsers, and the admin's actions. */
export function UsersSection() {
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const { data: me } = useMe();
  const users = useQuery({
    queryKey: [...usersKey, offset],
    queryFn: ({ signal }) => listUsers(offset, signal),
    placeholderData: keepPreviousData,
  });
  const page = users.data;
  const now = new Date();

  return (
    <section aria-labelledby="users-title" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="users-title" className="text-lg font-semibold tracking-tight">
          Accounts
        </h2>
        <p className="text-sm text-muted-foreground">
          Everyone who can sign in. Disabling an account signs it out of every
          browser at once.
        </p>
      </div>

      {users.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground"
        >
          <span className="delayed-appear inline-flex items-center gap-2">
            <Spinner aria-hidden="true" role={undefined} />
            Loading accounts
          </span>
        </p>
      ) : users.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>The accounts could not be loaded. {message(users.error)}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void users.refetch()}
              disabled={users.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : page && page.total === 0 ? (
        <Empty className="flex-none gap-2 border px-4 py-10 md:px-4 md:py-10">
          <EmptyHeader className="max-w-none">
            <EmptyMedia className="mb-0 text-muted-foreground">
              <Users aria-hidden="true" className="size-6" />
            </EmptyMedia>
            <EmptyTitle className="text-base tracking-normal">
              No accounts yet
            </EmptyTitle>
            <EmptyDescription className="max-w-sm">
              People you invite appear here once they accept their link.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : page ? (
        <>
          <UsersTable
            items={page.items}
            now={now}
            meId={me?.id}
            lastAdmin={page.active_admins <= 1}
            busy={users.isPlaceholderData}
            onAction={(user, action) => {
              setPending({ user, action, self: user.id === me?.id });
            }}
          />
          {page.total > USERS_PAGE_SIZE ? (
            <nav
              aria-label="Accounts pages"
              className="flex items-center justify-between gap-2 text-sm text-muted-foreground"
            >
              <span>
                {page.offset + 1} to {page.offset + page.items.length} of{" "}
                {page.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page.offset === 0}
                  onClick={() => {
                    setOffset(Math.max(0, page.offset - USERS_PAGE_SIZE));
                  }}
                >
                  <ChevronLeft aria-hidden="true" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page.offset + page.items.length >= page.total}
                  onClick={() => {
                    setOffset(page.offset + USERS_PAGE_SIZE);
                  }}
                >
                  Next
                  <ChevronRight aria-hidden="true" />
                </Button>
              </div>
            </nav>
          ) : null}
        </>
      ) : null}

      <ConfirmDialog
        pending={pending}
        onClose={() => {
          setPending(null);
        }}
      />
    </section>
  );
}

function UsersTable({
  items,
  now,
  meId,
  lastAdmin,
  busy,
  onAction,
}: {
  items: AdminUser[];
  now: Date;
  meId: string | undefined;
  lastAdmin: boolean;
  busy: boolean;
  onAction: (user: AdminUser, action: Action) => void;
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
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((user) => {
          const self = user.id === meId;
          const protectedAdmin =
            lastAdmin && user.is_active && user.role === "admin";
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
                {user.is_active ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="size-2 rounded-full bg-success"
                    />
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
                )}
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
              <TableCell className="text-right max-sm:pt-2 max-sm:text-left">
                <ActionsMenu
                  user={user}
                  self={self}
                  protectedAdmin={protectedAdmin}
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

function ActionsMenu({
  user,
  self,
  protectedAdmin,
  onAction,
}: {
  user: AdminUser;
  self: boolean;
  protectedAdmin: boolean;
  onAction: (user: AdminUser, action: Action) => void;
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

function copy({ user, action, self }: Pending) {
  const name = displayName(user);
  switch (action) {
    case "promote":
      return {
        title: `Make ${name} an admin?`,
        body: `${name} can then invite people, send reset links and manage every account, yours included.`,
        confirm: "Make admin",
        busy: "Saving",
        done: `${name} is now an admin.`,
      };
    case "demote":
      return self
        ? {
            title: "Give up your admin role?",
            body: "You lose the admin area at once and stay signed in as a member. Another admin can make you an admin again.",
            confirm: "Make me a member",
            busy: "Saving",
            done: "You are now a member.",
          }
        : {
            title: `Make ${name} a member?`,
            body: `${name} loses the admin area at once and stays signed in as a member.`,
            confirm: "Make member",
            busy: "Saving",
            done: `${name} is now a member.`,
          };
    case "disable":
      return {
        title: `Disable ${name}?`,
        body: `${name} is signed out of every browser at once and cannot sign in until you enable the account again. Their data stays.`,
        confirm: "Disable",
        busy: "Disabling",
        done: `${name} is disabled and signed out everywhere.`,
      };
    case "enable":
      return {
        title: `Enable ${name}?`,
        body: `${name} can sign in again with their password. Browsers that were signed in before stay signed out.`,
        confirm: "Enable",
        busy: "Enabling",
        done: `${name} can sign in again.`,
      };
  }
}

function ConfirmDialog({
  pending,
  onClose,
}: {
  pending: Pending | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [error, setError] = useState<string>();
  const mutation = useMutation({
    mutationFn: ({ user, action }: Pending) =>
      updateUser(user.id, CHANGE[action]),
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: async (_user, done) => {
      toast.success(copy(done).done);
      onClose();
      if (done.self && done.action === "demote") {
        // No longer an admin: leave the admin area and let /me drop the Admin entry.
        await navigate("/jobs", { replace: true });
        await client.refetchQueries({ queryKey: meQueryKey });
        client.removeQueries({ queryKey: usersKey });
        client.removeQueries({ queryKey: invitesKey });
        return;
      }
      await client.invalidateQueries({ queryKey: usersKey });
    },
    onError: (failure) => {
      void client.invalidateQueries({ queryKey: usersKey });
      if (isApiError(failure) && failure.status === 404) {
        toast.error("This account no longer exists.");
        onClose();
        return;
      }
      setError(message(failure));
    },
  });

  const text = pending ? copy(pending) : null;
  const destructive =
    pending?.action === "disable" || pending?.action === "demote";

  return (
    <AlertDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) {
          setError(undefined);
          onClose();
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="break-words">
            {text?.title}
          </AlertDialogTitle>
          <AlertDialogDescription className="break-words">
            {text?.body}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={mutation.isPending}>
            Cancel
          </AlertDialogCancel>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={mutation.isPending}
            aria-busy={mutation.isPending}
            onClick={() => {
              if (pending) mutation.mutate(pending);
            }}
          >
            {mutation.isPending ? (
              <>
                <Spinner aria-hidden="true" role={undefined} />
                {text?.busy}
              </>
            ) : (
              text?.confirm
            )}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
