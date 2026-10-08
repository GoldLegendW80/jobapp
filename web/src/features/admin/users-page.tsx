import { useTitle } from "@/lib/use-title";

import { InvitesSection } from "./invites-section";
import { UsersSection } from "./users-section";

/** The admin home: the accounts, then the invite and reset links. */
export function AdminUsersPage() {
  useTitle("Users");
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-muted-foreground">
          Manage who can sign in, invite people, send password reset links and
          revoke links that are no longer needed.
        </p>
      </header>
      <UsersSection />
      <InvitesSection />
    </div>
  );
}
