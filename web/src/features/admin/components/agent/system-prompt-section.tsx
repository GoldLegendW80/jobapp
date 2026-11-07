import { Textarea } from "@/components/ui/textarea";

import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import { AgentField } from "./agent-field";
import { AgentSection } from "./agent-section";
import { FoldedJson } from "./folded-json";
import { PromptDiff } from "./prompt-diff";

/** The system prompt, what it changes from the code's, and the code's own; the code's guards beside it. */
export function SystemPromptSection({
  page,
  draft,
  problems,
  onEdit,
}: DraftFieldProps) {
  return (
    <AgentSection title="System prompt" description={page.guards}>
      <AgentField
        label="System prompt"
        hideLabel
        error={problems.system_prompt}
        hint={`${draft.system_prompt.length.toLocaleString("en")} of ${page.choices.prompt_max.toLocaleString("en")} characters. Only its size is checked, never what it says.`}
        changed={draft.system_prompt !== page.code.system_prompt}
      >
        {(id, described) => (
          <Textarea
            id={id}
            aria-describedby={described}
            rows={16}
            className="font-mono text-xs"
            value={draft.system_prompt}
            onChange={(event) => {
              onEdit({ system_prompt: event.target.value });
            }}
          />
        )}
      </AgentField>
      <PromptDiff from={page.code.system_prompt} to={draft.system_prompt} />
      <FoldedJson
        label="The code's prompt"
        value={page.code.system_prompt}
        text
      />
    </AgentSection>
  );
}
