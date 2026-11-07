import { Textarea } from "@/components/ui/textarea";

import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import { AgentField } from "./agent-field";
import { AgentSection } from "./agent-section";

/** What a run first says to the agent, and its default message when it has one. */
export function FirstMessageSection({
  page,
  draft,
  problems,
  onEdit,
}: DraftFieldProps) {
  return (
    <AgentSection
      title="First message"
      description="What a run first says to the agent."
    >
      <p className="max-w-prose text-sm">{page.first_message}</p>
      {page.uses_default_message ? (
        <AgentField
          label="Default message"
          error={problems.default_message}
          hint="Used when a run's input carries no message of its own."
          code={page.code.default_message || "None"}
          changed={draft.default_message !== page.code.default_message}
        >
          {(id, described) => (
            <Textarea
              id={id}
              aria-describedby={described}
              rows={3}
              value={draft.default_message}
              onChange={(event) => {
                onEdit({ default_message: event.target.value });
              }}
            />
          )}
        </AgentField>
      ) : null}
    </AgentSection>
  );
}
