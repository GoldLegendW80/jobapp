import { ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

import type { AgentPage } from "../../api/agents-api";
import { useAgentDraft } from "@/features/admin/api/use-agent-draft";
import { ClaudeCodeSection } from "./claude-code-section";
import { CodeChangedNotice } from "./code-changed-notice";
import { FirstMessageSection } from "./first-message-section";
import { IdentitySection } from "./identity-section";
import { ModelSection } from "./model-section";
import { ParametersSection } from "./parameters-section";
import { RecentRunsSection } from "./recent-runs-section";
import { RevertDialog } from "./revert-dialog";
import { SaveSection } from "./save-section";
import { SystemPromptSection } from "./system-prompt-section";
import { ToolsSection } from "./tools-section";
import { VersionHistorySection } from "./version-history-section";

/** One agent: everything that makes it, its editable parts as a draft, and their save. */
export function AgentView({ page }: { page: AgentPage }) {
  const agent = useAgentDraft(page);
  const fields = {
    page,
    draft: agent.draft,
    problems: agent.problems,
    onEdit: agent.edit,
  };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <PageHeader
          title={page.name}
          description={page.effective.description}
        />
        <div className="flex flex-wrap gap-1">
          {page.test_only ? <Badge variant="outline">Test only</Badge> : null}
          {page.internal ? <Badge variant="outline">Internal</Badge> : null}
          {page.private_io ? <Badge variant="outline">Private</Badge> : null}
          <Badge variant="secondary">
            {Object.keys(page.saved).length && page.version !== null
              ? `Runs use version ${String(page.version)}`
              : "Runs use the code's definition"}
          </Badge>
        </div>
      </div>

      {page.code_changed.length ? (
        <CodeChangedNotice
          page={page}
          onRevert={agent.askRevert}
          busy={agent.saving}
        />
      ) : null}

      <Alert>
        <ShieldCheck aria-hidden="true" />
        <AlertDescription>{page.guards}</AlertDescription>
      </Alert>

      <IdentitySection {...fields} />
      <ParametersSection page={page} />
      <SystemPromptSection {...fields} />
      <FirstMessageSection {...fields} />
      <ToolsSection {...fields} />
      <ModelSection {...fields} />
      <SaveSection page={page} agent={agent} />
      <ClaudeCodeSection page={page} />
      <RecentRunsSection page={page} />
      <VersionHistorySection page={page} />

      <RevertDialog
        name={page.name}
        open={agent.reverting}
        error={agent.error}
        busy={agent.saving}
        onCancel={agent.cancelRevert}
        onRevert={agent.revert}
      />
    </div>
  );
}
