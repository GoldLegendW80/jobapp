import { Input } from "@/components/ui/input";

import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import { AgentField } from "./agent-field";
import { AgentSection } from "./agent-section";

/** Where the agent lives in the code, who starts it, its flags, and its description. */
export function IdentitySection({
  page,
  draft,
  problems,
  onEdit,
}: DraftFieldProps) {
  return (
    <AgentSection id="identity" title="Identity">
      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-muted-foreground">Module</dt>
        <dd>
          <code className="text-xs [overflow-wrap:anywhere]">
            {page.module}
          </code>
        </dd>
        <dt className="text-muted-foreground">Started by</dt>
        <dd>{page.starter}</dd>
      </dl>
      <ul className="list-disc pl-5 text-sm">
        {page.flags.map((flag) => (
          <li key={flag}>{flag}</li>
        ))}
      </ul>
      <AgentField
        label="Description"
        error={problems.description}
        code={page.code.description}
        changed={draft.description !== page.code.description}
      >
        {(id, described) => (
          <Input
            id={id}
            aria-describedby={described}
            value={draft.description}
            maxLength={page.choices.description_max}
            onChange={(event) => {
              onEdit({ description: event.target.value });
            }}
          />
        )}
      </AgentField>
    </AgentSection>
  );
}
