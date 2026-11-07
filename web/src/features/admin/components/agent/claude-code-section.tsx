import type { AgentPage } from "../../api/agents-api";
import { AgentSection } from "./agent-section";

/** The options the runner hands the Claude Agent SDK, read only. */
export function ClaudeCodeSection({ page }: { page: AgentPage }) {
  return (
    <AgentSection
      title="How Claude Code is run for it"
      description="The options the runner hands the Claude Agent SDK for each run, as they are. None of them can be edited, except through the fields above."
    >
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[9rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Environment</dt>
        <dd>{page.environment}</dd>
        <dt className="text-muted-foreground">Tool gate</dt>
        <dd>{page.gate}</dd>
        <dt className="text-muted-foreground">Skills</dt>
        <dd>{page.skills}</dd>
      </dl>
      <pre
        aria-label="Claude Agent SDK options"
        className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs"
      >
        {JSON.stringify(page.claude_code, null, 2)}
      </pre>
    </AgentSection>
  );
}
