import { Bot } from "lucide-react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";
import { isApiError } from "@/lib/api";

import { useAdminAgent } from "../api/use-admin-agents";
import { AgentView } from "../components/agent/agent-view";
import { AGENT_TRAIL } from "../helpers/agent-labels";

/** Admin, Agents, one agent: everything that makes it, and its editable parts. */
export function AdminAgentPage() {
  const { name = "" } = useParams();
  useTitle(`Agent ${name}`);
  const agent = useAdminAgent(name);
  if (agent.data)
    // Keyed by agent: the draft is set from the page once, so another agent's page
    // starts from its own values, never from the last one's draft.
    return <AgentView key={agent.data.name} page={agent.data} />;
  return (
    <Page>
      <PageHeader trail={AGENT_TRAIL} title={name} />
      <PageBody>
        {agent.isPending ? (
          <LoadingCard>Loading the agent</LoadingCard>
        ) : isApiError(agent.error) && agent.error.status === 404 ? (
          <EmptyState
            icon={Bot}
            title="No agent has this name"
            description="It may have been renamed or removed from the code."
            actions={
              <Button asChild>
                <Link to="/admin/agents">See all agents</Link>
              </Button>
            }
          />
        ) : (
          <LoadError query={agent}>The agent could not be loaded.</LoadError>
        )}
      </PageBody>
    </Page>
  );
}
