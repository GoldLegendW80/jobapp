import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

import { prettyJson } from "@/features/jobs/helpers/text";

/** The fields an import brought that the app has no place for, folded until asked for. */
export function ImportedFields({ fields }: { fields: [string, unknown][] }) {
  const [open, setOpen] = useState(false);
  const them =
    fields.length === 1 ? "the field" : `the ${fields.length} fields`;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Section
        title="Imported fields"
        description="What an import brought that the app has no place for."
        actions={
          <CollapsibleTrigger asChild>
            <Button variant="outline" size="sm" className="group">
              <ChevronDown
                aria-hidden="true"
                className="transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none"
              />
              {open ? "Hide" : "Show"} {them}
            </Button>
          </CollapsibleTrigger>
        }
      >
        <CollapsibleContent>
          <Card>
            <CardContent>
              <dl className="flex flex-col gap-3">
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
            </CardContent>
          </Card>
        </CollapsibleContent>
      </Section>
    </Collapsible>
  );
}
