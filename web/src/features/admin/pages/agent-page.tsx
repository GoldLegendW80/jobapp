import { ArrowLeft, CircleAlert, RotateCw } from "lucide-react";
import { Link, useParams } from "react-router";

import { LoadingCard } from "@/components/blocks/loading-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";
import { isApiError } from "@/lib/api";

import { useAdminAgent } from "../api/use-admin-agents";
import { AdminTabs } from "@/components/shared/admin/admin-tabs";
import { AgentView } from "../components/agent/agent-view";
import { message } from "../helpers/errors";

/** Admin, Agents, one agent: everything that makes it, and its editable parts. */
export function AdminAgentPage() {
  const { name = "" } = useParams();
  useTitle(`Agent ${name}`);
  const agent = useAdminAgent(name);
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <AdminTabs />
      <Button
        asChild
        variant="ghost"
        size="sm"
        className="-ml-3 self-start text-muted-foreground"
      >
        <Link to="/admin/agents">
          <ArrowLeft aria-hidden="true" />
          All agents
        </Link>
      </Button>
      {agent.isPending ? (
        <LoadingCard>Loading the agent</LoadingCard>
      ) : agent.data ? (
        // Keyed by agent: the draft is set from the page once, so another agent's page
        // starts from its own values, never from the last one's draft.
        <AgentView key={agent.data.name} page={agent.data} />
      ) : (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col items-start gap-3">
            <p>
              {isApiError(agent.error) && agent.error.status === 404
                ? "No agent has this name."
                : `The agent could not be loaded. ${message(agent.error)}`}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void agent.refetch()}
              disabled={agent.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
