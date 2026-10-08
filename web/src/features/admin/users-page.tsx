import { useTitle } from "@/lib/use-title";

import { InvitesSection } from "./invites-section";

/** The admin home: invites now; the users table (I5) goes above them. */
export function AdminUsersPage() {
  useTitle("Users");
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-muted-foreground">
          Invite people, send password reset links and revoke links that are no
          longer needed.
        </p>
      </header>
      <InvitesSection />
    </div>
  );
}
