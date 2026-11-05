import { NavLink } from "react-router";

import { cn } from "@/lib/utils";

const tab = ({ isActive }: { isActive: boolean }) =>
  cn(
    "-mb-px border-b-2 px-3 py-2 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring",
    isActive
      ? "border-primary text-foreground"
      : "border-transparent text-muted-foreground hover:text-foreground",
  );

/** The tabs of Admin: the accounts and invites, then the agents (agents A6). */
export function AdminTabs() {
  return (
    <nav aria-label="Admin" className="flex gap-1 border-b">
      <NavLink to="/admin/users" className={tab}>
        Users
      </NavLink>
      <NavLink to="/admin/agents" className={tab}>
        Agents
      </NavLink>
    </nav>
  );
}
