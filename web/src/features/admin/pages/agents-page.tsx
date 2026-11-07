import { Bot } from "lucide-react";
import { Fragment } from "react";
import { Link } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Section } from "@/components/blocks/section";
import { AdminFrame } from "@/components/shared/admin/admin-frame";
import { Moment } from "@/components/shared/admin/moment";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useTitle } from "@/hooks/use-title";

import { type AgentList, type AgentSummary } from "../api/agents-api";
import { useAdminAgents } from "../api/use-admin-agents";

/**
 * Admin, Agents: every agent of the app, what it runs on and when it last ran, grouped by
 * category (agents A8).
 */
export function AdminAgentsPage() {
  useTitle("Agents");
  const agents = useAdminAgents();
  const now = new Date();
  return (
    <AdminFrame>
      {agents.isPending ? (
        <LoadingCard>Loading agents</LoadingCard>
      ) : agents.isError && !agents.data ? (
        <LoadError query={agents}>The agents could not be loaded.</LoadError>
      ) : (
        <AgentGroups list={agents.data} now={now} />
      )}
    </AdminFrame>
  );
}

/** One section of the list: a category and its agents, in the api's order. */
interface AgentGroup {
  key: string;
  title: string;
  sentence: string;
  agents: AgentSummary[];
}

const OTHER: Omit<AgentGroup, "agents"> = {
  key: "other",
  title: "Other",
  sentence: "These agents have no category the list knows.",
};

/**
 * The sections of the list (agents A8): one per category that has an agent, in the order of
 * `categories`, each agent in its category's section in the api's order (by name). An agent
 * whose category the answer does not list, as from an api older than the page, comes last
 * under Other instead of being left out.
 */
function groupAgents({ items, categories }: AgentList): AgentGroup[] {
  const groups = categories.map((category) => ({
    ...category,
    agents: items.filter((agent) => agent.category === category.key),
  }));
  const known = new Set(categories.map((category) => category.key));
  const other = items.filter((agent) => !known.has(agent.category));
  if (other.length) groups.push({ ...OTHER, agents: other });
  return groups.filter((group) => group.agents.length > 0);
}

function AgentGroups({ list, now }: { list: AgentList; now: Date }) {
  const groups = groupAgents(list);
  if (!groups.length)
    return (
      <EmptyState
        icon={Bot}
        title="No agent is registered"
        description="The agents the code defines appear here, with their model, version and last run."
      />
    );
  return (
    <>
      {groups.map((group) => (
        <Section
          key={group.key}
          title={
            <>
              {group.title}{" "}
              <span className="font-normal text-muted-foreground tabular-nums">
                ({group.agents.length})
              </span>
            </>
          }
          description={group.sentence}
        >
          <AgentsTable title={group.title} items={group.agents} now={now} />
        </Section>
      ))}
    </>
  );
}

/**
 * The column widths shared by the tables of the list, so their columns line up from one section
 * to the next (`lg:table-fixed`); Agent takes what is left and its descriptions wrap inside it.
 * Model and Last run stay on one line. From 1024px up only: below, the table lays itself out.
 */
const COLUMNS = [
  { label: "Agent", className: "" },
  { label: "Module", className: "lg:w-52 xl:w-64" },
  { label: "Model", className: "lg:w-40 xl:w-48 lg:whitespace-nowrap" },
  { label: "Version", className: "lg:w-44 xl:w-56 lg:whitespace-nowrap" },
  { label: "Last run", className: "lg:w-32 xl:w-40 lg:whitespace-nowrap" },
];

function AgentsTable({
  title,
  items,
  now,
}: {
  title: string;
  items: AgentSummary[];
  now: Date;
}) {
  return (
    <Table className="lg:table-fixed">
      <TableCaption className="sr-only">{title}, by name</TableCaption>
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead key={column.label} className={column.className}>
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((agent) => (
          <TableRow key={agent.name} className="align-top">
            <TableHead className="font-normal">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Link
                  to={`/admin/agents/${encodeURIComponent(agent.name)}`}
                  className="font-medium underline-offset-4 hover:underline [overflow-wrap:anywhere]"
                >
                  {agent.name}
                </Link>
                {agent.test_only ? (
                  <Badge variant="outline">Test only</Badge>
                ) : null}
                {agent.internal ? (
                  <Badge variant="outline">Internal</Badge>
                ) : null}
                {agent.private_io ? (
                  <Badge variant="outline">Private</Badge>
                ) : null}
              </span>
              <p className="mt-0.5 max-w-prose text-sm text-muted-foreground [overflow-wrap:anywhere]">
                {agent.description}
              </p>
            </TableHead>
            <TableCell label="Module">
              <span className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">
                {agent.module.split(".").map((part, index) => (
                  <Fragment key={index}>
                    {index > 0 ? (
                      <>
                        .<wbr />
                      </>
                    ) : null}
                    {part}
                  </Fragment>
                ))}
              </span>
            </TableCell>
            <TableCell label="Model" className="lg:whitespace-nowrap">
              {agent.model}
            </TableCell>
            <TableCell label="Version" className="lg:whitespace-nowrap">
              <span className="flex flex-col gap-0.5">
                {agent.overridden && agent.version !== null
                  ? `Version ${String(agent.version)}`
                  : "The code's"}
                {agent.code_changed.length ? (
                  <span className="text-xs whitespace-normal text-muted-foreground">
                    {agent.code_changed.includes("system_prompt")
                      ? "The code's prompt changed since it was saved"
                      : "The code changed since it was saved"}
                  </span>
                ) : null}
              </span>
            </TableCell>
            <TableCell label="Last run" className="lg:whitespace-nowrap">
              {agent.last_run_at ? (
                <Moment at={agent.last_run_at} now={now} />
              ) : (
                "Never"
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
