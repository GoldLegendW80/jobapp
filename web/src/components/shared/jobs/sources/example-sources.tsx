import { Lightbulb, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { Protected } from "@/features/jobs/helpers/site-permissions";
import type { Example } from "@/features/jobs/helpers/sources";
import { BLOCK, Glyph, HANG } from "../setup-grid";

/** "Try one": the examples not in the list yet, one click each, and the recommended websites at
 * once while some are missing. */
export function ExampleSources({
  examples,
  offerRecommended,
  disabled,
  onAdd,
  onAddRecommended,
}: {
  examples: readonly Example[];
  offerRecommended: boolean;
  disabled: boolean;
  onAdd: (url: string, label: string, platform?: Protected) => void;
  onAddRecommended: () => void;
}) {
  return (
    <div className={BLOCK}>
      <p className={cn(HANG, "text-sm font-medium")}>
        <Glyph line="h-5">
          <Lightbulb className="size-4" />
        </Glyph>
        Try one
      </p>
      {offerRecommended ? (
        <Button
          type="button"
          size="sm"
          className="w-fit rounded-full"
          disabled={disabled}
          onClick={onAddRecommended}
        >
          <Plus aria-hidden="true" />
          Add the recommended websites
        </Button>
      ) : null}
      <ul aria-label="Examples" className="flex flex-wrap gap-2">
        {examples.map((example) => (
          <li key={example.url}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="rounded-full"
              disabled={disabled}
              aria-label={`Add ${example.label}`}
              onClick={() => {
                onAdd(example.url, example.label, example.platform);
              }}
            >
              <Plus aria-hidden="true" />
              {example.label}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
