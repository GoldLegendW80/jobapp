import { AdminFrame } from "@/components/shared/admin/admin-frame";
import { useTitle } from "@/hooks/use-title";

import { InvitesSection } from "../components/invites-section";
import { UsersSection } from "../components/users-section";

/** Admin, Users: the accounts, then the invite and reset links. */
export function AdminUsersPage() {
  useTitle("Users");
  return (
    <AdminFrame>
      <UsersSection />
      <InvitesSection />
    </AdminFrame>
  );
}
