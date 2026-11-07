import { Users } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Section } from "@/components/blocks/section";
import { useMe } from "@/features/auth/api/use-me";

import { USERS_PAGE_SIZE } from "../api/admin-api";
import { useUserBrowsers } from "../api/use-user-browsers";
import { useUsers } from "../api/use-users";
import type { PendingChange } from "../helpers/user-actions";
import { ListPager } from "./list-pager";
import { UserChangeDialog } from "./users/user-change-dialog";
import { UsersTable } from "./users/users-table";

/** Who can sign in: role, status, last sign in, signed in browsers, and the admin's actions. */
export function UsersSection() {
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const { data: me } = useMe();
  const users = useUsers(offset);
  const browsers = useUserBrowsers();
  const now = new Date();

  return (
    <Section
      title="Accounts"
      description="Everyone who can sign in. Disabling an account signs it out of every browser at once."
    >
      {users.isPending ? (
        <LoadingCard>Loading accounts</LoadingCard>
      ) : users.isError ? (
        <LoadError query={users}>The accounts could not be loaded.</LoadError>
      ) : users.data.total === 0 ? (
        <EmptyState
          icon={Users}
          title="No accounts yet"
          description="People you invite appear here once they accept their link."
        />
      ) : (
        <>
          <UsersTable
            items={users.data.items}
            now={now}
            meId={me?.id}
            lastAdmin={users.data.active_admins <= 1}
            busy={users.isPlaceholderData}
            browsersMax={users.data.browsers_max}
            savingBrowsers={browsers.saving}
            onBrowsers={browsers.set}
            onAction={(user, action) => {
              setPending({ user, action, self: user.id === me?.id });
            }}
          />
          {users.data.total > USERS_PAGE_SIZE ? (
            <ListPager
              label="Accounts pages"
              pageSize={USERS_PAGE_SIZE}
              offset={users.data.offset}
              shown={users.data.items.length}
              total={users.data.total}
              onOffset={setOffset}
            />
          ) : null}
        </>
      )}

      <UserChangeDialog
        pending={pending}
        onClose={() => {
          setPending(null);
        }}
      />
    </Section>
  );
}
