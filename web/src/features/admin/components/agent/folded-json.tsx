import { ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

/** A value shown as JSON (or as text) under a label that unfolds it. */
export function FoldedJson({
  label,
  value,
  text = false,
}: {
  label: string;
  value: unknown;
  text?: boolean;
}) {
  return (
    <Collapsible className="flex min-w-0 flex-col gap-1 text-sm">
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="group/fold -ml-2 w-fit gap-1.5 font-normal text-muted-foreground"
        >
          <ChevronRight
            aria-hidden="true"
            className="transition-transform group-data-[state=open]/fold:rotate-90 motion-reduce:transition-none"
          />
          {label}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
          {text ? String(value) : JSON.stringify(value, null, 2)}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
