import { ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

import type { StepBody as Body } from "@/features/agents/helpers/run-steps";

const RESULT_SHOWN = 600;

/** A tool's result, folded under a Show (or its length, when it is long). */
function ToolResult({ content }: { content: string }) {
  return (
    <Collapsible className="text-xs">
      <CollapsibleTrigger asChild>
        <Button
          variant="link"
          className="group/fold h-auto gap-1 p-0 text-xs font-normal text-muted-foreground has-[>svg]:px-0"
        >
          <ChevronRight
            aria-hidden="true"
            className="size-3 transition-transform group-data-[state=open]/fold:rotate-90 motion-reduce:transition-none"
          />
          {content.length > RESULT_SHOWN
            ? `${content.length} characters`
            : "Show"}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-1 max-h-60 overflow-auto rounded-md bg-muted p-2 font-mono whitespace-pre-wrap">
          {content.slice(0, RESULT_SHOWN * 4)}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** What one step of the timeline says, under its title. */
export function StepBody({ body }: { body: Body }) {
  switch (body.kind) {
    case "note":
      return (
        <p className="text-sm break-words text-muted-foreground">{body.text}</p>
      );
    case "answer":
      return (
        <p className="text-sm break-words whitespace-pre-wrap text-foreground">
          {body.text}
        </p>
      );
    case "arguments":
      return (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-xs">
          {body.entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-muted-foreground">{key}</dt>
              <dd className="font-mono break-all">{value}</dd>
            </div>
          ))}
        </dl>
      );
    case "size":
      return <span className="text-xs text-muted-foreground">{body.text}</span>;
    case "result":
      return <ToolResult content={body.content} />;
  }
}
