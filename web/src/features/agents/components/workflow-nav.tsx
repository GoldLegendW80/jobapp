import { NavLink } from "react-router";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { Workflow } from "../api/activity-api";

const LINK =
  "flex h-9 shrink-0 items-center rounded-md px-3 text-sm whitespace-nowrap outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[current=page]:bg-accent aria-[current=page]:font-medium md:whitespace-normal";

/**
 * The workflows on the side of Activity: All workflows, then each one. A row that scrolls
 * sideways on a phone, a column on a wider screen.
 */
export function WorkflowNav({
  workflows,
  loading,
}: {
  workflows: Workflow[];
  loading: boolean;
}) {
  return (
    <nav aria-label="Workflows" className="min-w-0">
      <ul className="flex gap-1 overflow-x-auto pb-1 md:flex-col md:overflow-visible md:pb-0">
        <li>
          <NavLink to="/activity" end className={LINK}>
            All workflows
          </NavLink>
        </li>
        {loading
          ? [0, 1, 2].map((index) => (
              <li key={index} className="px-3 py-2">
                <Skeleton className="h-4 w-32" />
              </li>
            ))
          : workflows.map((workflow) => (
              <li key={workflow.key}>
                <NavLink
                  to={`/activity/${encodeURIComponent(workflow.key)}`}
                  className={cn(LINK)}
                >
                  {workflow.title}
                </NavLink>
              </li>
            ))}
      </ul>
    </nav>
  );
}
