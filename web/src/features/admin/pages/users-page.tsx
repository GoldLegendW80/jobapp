import { PageHeader } from "@/components/blocks/page-header";
import { useTitle } from "@/hooks/use-title";

import { AdminTabs } from "@/components/shared/admin/admin-tabs";
import { InvitesSection } from "../components/invites-section";
import { UsersSection } from "../components/users-section";

/** The admin home: the accounts, then the invite and reset links. */
export function AdminUsersPage() {
  useTitle("Users");
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-10">
      <div className="flex flex-col gap-6">
        <AdminTabs />
        <PageHeader
          title="Users"
          description="Manage who can sign in, invite people, send password reset links and revoke links that are no longer needed."
        />
      </div>
      <UsersSection />
      <InvitesSection />
    </div>
  );
}
