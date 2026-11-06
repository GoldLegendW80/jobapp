import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";

/** The tabs of Admin: the accounts and invites, then the agents (agents A6). */
export function AdminTabs() {
  return (
    <PillNav label="Admin" className="w-fit">
      <PillNavLink to="/admin/users">Users</PillNavLink>
      <PillNavLink to="/admin/agents">Agents</PillNavLink>
    </PillNav>
  );
}
