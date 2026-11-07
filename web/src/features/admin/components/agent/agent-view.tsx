import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";

import type { AgentPage } from "../../api/agents-api";
import { useAgentDraft } from "@/features/admin/api/use-agent-draft";
import { AGENT_TRAIL } from "@/features/admin/helpers/agent-labels";
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
    <Page>
      <PageHeader
        trail={AGENT_TRAIL}
        title={page.name}
        description={page.effective.description}
      />
      <PageBody>
        {page.code_changed.length ? (
          <CodeChangedNotice
            page={page}
            onRevert={agent.askRevert}
            busy={agent.saving}
          />
        ) : null}
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
      </PageBody>

      <RevertDialog
        name={page.name}
        open={agent.reverting}
        error={agent.error}
        busy={agent.saving}
        onCancel={agent.cancelRevert}
        onRevert={agent.revert}
      />
    </Page>
  );
}
