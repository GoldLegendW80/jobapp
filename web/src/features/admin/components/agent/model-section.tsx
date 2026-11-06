import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { duration } from "../../api/agents-api";
import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import {
  numberOrNaN,
  withKindModel,
} from "@/features/admin/helpers/agent-draft";
import { CREDENTIAL_KIND_LABEL } from "@/features/admin/helpers/agent-labels";
import { AgentField } from "./agent-field";
import { AgentSection } from "./agent-section";

/** The model, per credential kind too, the effort, the turn and time limits, and what ends a run. */
export function ModelSection({
  page,
  draft,
  problems,
  onEdit,
}: DraftFieldProps) {
  return (
    <AgentSection id="model" title="Model and limits">
      <div className="grid gap-4 sm:grid-cols-2">
        <AgentField
          label="Model"
          code={page.code.model}
          changed={draft.model !== page.code.model}
        >
          {(id, described) => (
            <NativeSelect
              id={id}
              aria-describedby={described}
              value={draft.model}
              onChange={(event) => {
                onEdit({ model: event.target.value });
              }}
            >
              {page.choices.models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </NativeSelect>
          )}
        </AgentField>
        {page.choices.credential_kinds.map((kind) => (
          <AgentField
            key={kind}
            label={CREDENTIAL_KIND_LABEL[kind] ?? kind}
            code={page.code.model_by_credential[kind] ?? "The default model"}
            changed={
              draft.model_by_credential[kind] !==
              page.code.model_by_credential[kind]
            }
          >
            {(id, described) => (
              <NativeSelect
                id={id}
                aria-describedby={described}
                value={draft.model_by_credential[kind] ?? ""}
                onChange={(event) => {
                  const model = event.target.value;
                  onEdit((current) => withKindModel(current, kind, model));
                }}
              >
                <option value="">The default model</option>
                {page.choices.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </NativeSelect>
            )}
          </AgentField>
        ))}
        <AgentField
          label="Effort"
          code={page.code.effort ?? "Claude Code's default"}
          changed={draft.effort !== page.code.effort}
        >
          {(id, described) => (
            <NativeSelect
              id={id}
              aria-describedby={described}
              value={draft.effort ?? ""}
              onChange={(event) => {
                onEdit({ effort: event.target.value || null });
              }}
            >
              {page.code.effort === null ? (
                <option value="">Claude Code&apos;s default</option>
              ) : null}
              {page.choices.efforts.map((effort) => (
                <option key={effort} value={effort}>
                  {effort}
                </option>
              ))}
            </NativeSelect>
          )}
        </AgentField>
        <AgentField
          label="Max turns"
          error={problems.max_turns}
          hint={
            page.turn_budgeted
              ? "A run's input may lower it with its own turn budget."
              : undefined
          }
          code={String(page.code.max_turns)}
          changed={draft.max_turns !== page.code.max_turns}
        >
          {(id, described) => (
            <Input
              id={id}
              aria-describedby={described}
              type="number"
              inputMode="numeric"
              min={1}
              max={page.choices.turns_max}
              value={Number.isNaN(draft.max_turns) ? "" : draft.max_turns}
              onChange={(event) => {
                onEdit({ max_turns: numberOrNaN(event.target.value) });
              }}
            />
          )}
        </AgentField>
        <AgentField
          label="Wall time, in seconds"
          error={problems.wall_time_s}
          hint={`At most ${page.choices.wall_time_max_s.toLocaleString("en")} seconds (${duration(page.choices.wall_time_max_s)}) for this agent. ${page.choices.wall_time_where} Lower or raise it within that.`}
          code={String(page.code.wall_time_s)}
          changed={draft.wall_time_s !== page.code.wall_time_s}
        >
          {(id, described) => (
            <Input
              id={id}
              aria-describedby={described}
              type="number"
              inputMode="decimal"
              min={page.choices.wall_time_min_s}
              max={page.choices.wall_time_max_s}
              value={Number.isNaN(draft.wall_time_s) ? "" : draft.wall_time_s}
              onChange={(event) => {
                onEdit({ wall_time_s: numberOrNaN(event.target.value) });
              }}
            />
          )}
        </AgentField>
      </div>
      <h3 className="text-sm font-medium">What ends a run</h3>
      <ul className="list-disc pl-5 text-sm">
        {page.ends.map((end) => (
          <li key={end}>{end}</li>
        ))}
      </ul>
    </AgentSection>
  );
}
