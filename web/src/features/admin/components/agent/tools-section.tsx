import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

import type { DraftFieldProps } from "@/features/admin/api/use-agent-draft";
import { withTool } from "@/features/admin/helpers/agent-draft";
import { AgentSection } from "./agent-section";
import { FoldedJson } from "./folded-json";

/** The tools the code gives the agent, each one switched on or off: one row each, on one card. */
export function ToolsSection({
  page,
  draft,
  onEdit,
}: Omit<DraftFieldProps, "problems">) {
  return (
    <AgentSection
      title="Tools"
      description="The tools the code gives this agent, as the model sees them. Switch one off and the next runs neither offer nor serve it. No tool can be added here."
      surface="own"
    >
      <Card className="gap-0 py-0">
        <ul aria-label="Tools of this agent" className="divide-y">
          {page.tools.map((tool) => (
            <li
              key={tool.name}
              className="flex min-w-0 flex-col gap-1 px-6 py-4 text-sm"
            >
              <Label className="flex min-w-0 items-center gap-2">
                <Checkbox
                  checked={draft.tools.includes(tool.name)}
                  onCheckedChange={(checked) => {
                    onEdit((current) =>
                      withTool(
                        page.code.tools,
                        current,
                        tool.name,
                        checked === true,
                      ),
                    );
                  }}
                />
                <code className="[overflow-wrap:anywhere]">{tool.name}</code>
                {tool.on !== draft.tools.includes(tool.name) ? (
                  <Badge variant="outline">Not saved</Badge>
                ) : null}
              </Label>
              <div className="flex min-w-0 flex-col gap-1 pl-6">
                <p className="max-w-prose text-muted-foreground [overflow-wrap:anywhere]">
                  {tool.description ??
                    "Not described yet: the worker describes the tools it has loaded, a minute after it starts at most."}
                </p>
                <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
                  <code>{tool.full_name}</code>
                  {tool.module ? ` in ${tool.module}` : null}
                </p>
                {tool.input_schema ? (
                  <FoldedJson
                    label="Its input schema"
                    value={tool.input_schema}
                  />
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </AgentSection>
  );
}
