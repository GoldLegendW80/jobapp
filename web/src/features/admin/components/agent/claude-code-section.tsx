import type { AgentPage } from "../../api/agents-api";
import { AgentSection } from "./agent-section";

/** The options the runner hands the Claude Agent SDK, read only. */
export function ClaudeCodeSection({ page }: { page: AgentPage }) {
  return (
    <AgentSection id="claude-code" title="How Claude Code is run for it">
      <p className="text-sm text-muted-foreground">
        The options the runner hands the Claude Agent SDK for each run, as they
        are. None of them is editable, except through the fields above.
      </p>
      <pre
        aria-label="Claude Agent SDK options"
        className="max-h-96 overflow-auto rounded-md border bg-muted p-3 text-xs"
      >
        {JSON.stringify(page.claude_code, null, 2)}
      </pre>
      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-muted-foreground">Environment</dt>
        <dd>{page.environment}</dd>
        <dt className="text-muted-foreground">Tool gate</dt>
        <dd>{page.gate}</dd>
        <dt className="text-muted-foreground">Skills</dt>
        <dd>{page.skills}</dd>
      </dl>
    </AgentSection>
  );
}
