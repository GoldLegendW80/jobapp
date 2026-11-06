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
    <Collapsible className="rounded-md border text-sm">
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="group/fold h-auto w-full justify-start gap-1.5 rounded-md px-3 py-2 text-left font-normal whitespace-normal text-muted-foreground has-[>svg]:px-3"
        >
          <ChevronRight
            aria-hidden="true"
            className="transition-transform group-data-[state=open]/fold:rotate-90 motion-reduce:transition-none"
          />
          {label}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="max-h-96 overflow-auto border-t bg-muted p-3 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
          {text ? String(value) : JSON.stringify(value, null, 2)}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
