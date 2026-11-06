import { CircleAlert, RotateCw, Users } from "lucide-react";
import { useState } from "react";

import { LoadingCard } from "@/components/blocks/loading-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { useMe } from "@/features/auth/api/use-me";

import { USERS_PAGE_SIZE } from "../api/admin-api";
import { useUsers } from "../api/use-users";
import { message } from "../helpers/errors";
import type { PendingChange } from "../helpers/user-actions";
import { UserChangeDialog } from "./users/user-change-dialog";
import { UsersPager } from "./users/users-pager";
import { UsersTable } from "./users/users-table";

/** Who can sign in: role, status, last sign in, signed in browsers, and the admin's actions. */
export function UsersSection() {
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const { data: me } = useMe();
  const users = useUsers(offset);
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
        <LoadingCard>Loading accounts</LoadingCard>
      ) : users.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col items-start gap-3">
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
        <NoAccounts />
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
            <UsersPager
              offset={page.offset}
              shown={page.items.length}
              total={page.total}
              onOffset={setOffset}
            />
          ) : null}
        </>
      ) : null}

      <UserChangeDialog
        pending={pending}
        onClose={() => {
          setPending(null);
        }}
      />
    </section>
  );
}

function NoAccounts() {
  return (
    <Card className="py-0">
      <Empty className="flex-none gap-2 px-4 py-10 md:px-4 md:py-10">
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
    </Card>
  );
}
