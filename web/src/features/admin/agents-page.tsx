import { useQuery } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle, RotateCw } from "lucide-react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useTitle } from "@/lib/use-title";

import { AdminTabs } from "./admin-tabs";
import {
  agentsKey,
  listAgents,
  type AgentList,
  type AgentSummary,
} from "./agents-api";
import { message } from "./errors";
import { exactTime, relativeTime } from "./relative-time";

/**
 * Admin, Agents: every agent of the app, what it runs on and when it last ran, grouped by
 * category (agents A8).
 */
export function AdminAgentsPage() {
  useTitle("Agents");
  const agents = useQuery({
    queryKey: agentsKey,
    queryFn: ({ signal }) => listAgents(signal),
  });
  const now = new Date();
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="text-muted-foreground">
          Every agent of the app, as the code defines it and with what you
          saved. Open one to see everything that makes it, and to change its
          prompt, model, limits or tools for its next runs.
        </p>
      </header>
      <AdminTabs />
      {agents.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground"
        >
          <span className="delayed-appear inline-flex items-center gap-2">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading agents
          </span>
        </p>
      ) : agents.isError && !agents.data ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>The agents could not be loaded. {message(agents.error)}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void agents.refetch()}
              disabled={agents.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <AgentGroups list={agents.data} now={now} />
      )}
    </div>
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
      <p className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground">
        No agent is registered here.
      </p>
    );
  return (
    <div className="flex flex-col gap-8">
      {groups.map((group) => (
        <section
          key={group.key}
          aria-labelledby={`agents-${group.key}-title`}
          className="flex flex-col gap-4"
        >
          <div className="flex flex-col gap-1">
            <h2
              id={`agents-${group.key}-title`}
              className="text-lg font-semibold tracking-tight"
            >
              {group.title}{" "}
              <span className="font-normal text-muted-foreground tabular-nums">
                ({group.agents.length})
              </span>
            </h2>
            <p className="text-sm text-muted-foreground">{group.sentence}</p>
          </div>
          <AgentsTable title={group.title} items={group.agents} now={now} />
        </section>
      ))}
    </div>
  );
}

/**
 * The column widths shared by the six tables of the list, so their columns line up from one
 * section to the next (`sm:table-fixed`); Agent takes what is left and its descriptions wrap
 * inside it. Model, Version and Last run stay on one line.
 */
const COLUMNS = [
  { label: "Agent", className: "" },
  { label: "Module", className: "sm:w-60" },
  { label: "Model", className: "sm:w-44 sm:whitespace-nowrap" },
  { label: "Version", className: "sm:w-32 sm:whitespace-nowrap" },
  { label: "Last run", className: "sm:w-28 sm:whitespace-nowrap" },
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
    <Table className="sm:table-fixed">
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
              <Link
                to={`/admin/agents/${encodeURIComponent(agent.name)}`}
                className="font-medium text-primary underline-offset-4 hover:underline [overflow-wrap:anywhere]"
              >
                {agent.name}
              </Link>
              <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
                {agent.description}
              </p>
              <span className="mt-1 flex flex-wrap gap-1">
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
            </TableHead>
            <TableCell label="Module">
              <code className="text-xs max-sm:[overflow-wrap:anywhere]">
                {agent.module}
              </code>
            </TableCell>
            <TableCell label="Model" className="sm:whitespace-nowrap">
              {agent.model}
            </TableCell>
            <TableCell label="Version" className="sm:whitespace-nowrap">
              {agent.overridden && agent.version !== null
                ? `Version ${String(agent.version)}`
                : "The code's"}
              {agent.code_changed.length ? (
                <span className="mt-1 flex">
                  {/* Wraps on a phone's card line instead of widening it. */}
                  <Badge
                    variant="outline"
                    className="w-auto justify-start whitespace-normal"
                  >
                    {agent.code_changed.includes("system_prompt")
                      ? "The code's prompt changed since it was saved"
                      : "The code changed since it was saved"}
                  </Badge>
                </span>
              ) : null}
            </TableCell>
            <TableCell label="Last run" className="sm:whitespace-nowrap">
              {agent.last_run_at ? (
                <time
                  dateTime={agent.last_run_at}
                  title={exactTime(agent.last_run_at)}
                >
                  {relativeTime(agent.last_run_at, now)}
                </time>
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
