import { Input } from "@/components/ui/input";

import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import { AgentField } from "./agent-field";
import { AgentSection } from "./agent-section";

/** Where the agent lives in the code, who starts it, which version runs, its flags, and its description. */
export function IdentitySection({
  page,
  draft,
  problems,
  onEdit,
}: DraftFieldProps) {
  return (
    <AgentSection
      title="Identity"
      description="Where it lives in the code, who starts it and which version its runs use."
    >
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[9rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Module</dt>
        <dd className="font-mono text-xs [overflow-wrap:anywhere] sm:pt-0.5">
          {page.module}
        </dd>
        <dt className="text-muted-foreground">Started by</dt>
        <dd>{page.starter}</dd>
        <dt className="text-muted-foreground">Runs use</dt>
        <dd>
          {Object.keys(page.saved).length && page.version !== null
            ? `Version ${String(page.version)}`
            : "The code's definition"}
        </dd>
      </dl>
      {page.flags.length ? (
        <ul className="list-disc pl-5 text-sm text-muted-foreground">
          {page.flags.map((flag) => (
            <li key={flag}>{flag}</li>
          ))}
        </ul>
      ) : null}
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
