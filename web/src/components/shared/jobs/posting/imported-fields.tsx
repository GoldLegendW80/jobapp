import { ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

import { prettyJson } from "@/features/jobs/helpers/text";

/** The fields an import brought that the app has no place for, folded. */
export function ImportedFields({ fields }: { fields: [string, unknown][] }) {
  return (
    <Collapsible className="rounded-lg border">
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          className="group h-auto w-full justify-start gap-1 rounded-lg px-3 py-2 has-[>svg]:px-3 data-[state=open]:rounded-b-none"
        >
          <ChevronRight
            aria-hidden="true"
            className="transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none"
          />
          Imported fields
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent asChild>
        <dl className="flex flex-col gap-3 border-t px-3 py-3">
          {fields.map(([key, value]) => (
            <div key={key} className="flex flex-col gap-0.5">
              <dt className="text-xs font-medium text-muted-foreground">
                {key}
              </dt>
              <dd className="text-sm break-words whitespace-pre-wrap">
                {typeof value === "string" ? value : prettyJson(value)}
              </dd>
            </div>
          ))}
        </dl>
      </CollapsibleContent>
    </Collapsible>
  );
}
