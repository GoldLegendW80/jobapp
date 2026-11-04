import { useQuery } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle, RotateCw } from "lucide-react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/lib/use-title";

import { AdminTabs } from "./admin-tabs";
import { agentsKey, listAgents, type AgentSummary } from "./agents-api";
import { message } from "./errors";
import { exactTime, relativeTime } from "./relative-time";
import { cell } from "./table-cell";

/** Admin, Agents: every agent of the app, what it runs on and when it last ran. */
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
        <AgentsTable items={agents.data} now={now} />
      )}
    </div>
  );
}

function AgentsTable({ items, now }: { items: AgentSummary[]; now: Date }) {
  return (
    <table className="w-full text-sm" aria-label="Agents, by name">
      <thead className="max-sm:sr-only">
        <tr className="border-b text-left text-muted-foreground">
          <th scope="col" className="px-3 py-2 font-medium">
            Agent
          </th>
          <th scope="col" className="px-3 py-2 font-medium">
            Module
          </th>
          <th scope="col" className="px-3 py-2 font-medium">
            Model
          </th>
          <th scope="col" className="px-3 py-2 font-medium">
            Version
          </th>
          <th scope="col" className="px-3 py-2 font-medium">
            Last run
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((agent) => (
          <tr
            key={agent.name}
            className="border-b align-top max-sm:flex max-sm:flex-col max-sm:gap-1 max-sm:py-3"
          >
            <th
              scope="row"
              className="px-3 py-2 text-left font-normal max-sm:px-0"
            >
              <Link
                to={`/admin/agents/${encodeURIComponent(agent.name)}`}
                className="font-medium text-primary underline-offset-4 hover:underline [overflow-wrap:anywhere]"
              >
                {agent.name}
              </Link>
              <p className="text-muted-foreground [overflow-wrap:anywhere]">
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
            </th>
            <td {...cell("Module")}>
              <code className="text-xs [overflow-wrap:anywhere]">
                {agent.module}
              </code>
            </td>
            <td {...cell("Model")}>{agent.model}</td>
            <td {...cell("Version")}>
              {agent.overridden && agent.version !== null
                ? `Version ${String(agent.version)}`
                : "The code's"}
              {agent.code_changed.length ? (
                <span className="mt-1 flex">
                  <Badge variant="outline">
                    {agent.code_changed.includes("system_prompt")
                      ? "The code's prompt changed since it was saved"
                      : "The code changed since it was saved"}
                  </Badge>
                </span>
              ) : null}
            </td>
            <td {...cell("Last run")}>
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
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
