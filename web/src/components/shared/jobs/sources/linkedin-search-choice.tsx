import { useId } from "react";

import { FieldDescription, FieldLegend, FieldSet } from "@/components/ui/field";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { LinkedInSearch } from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";

import { HANG } from "../setup-grid";

const LINKEDIN_SEARCH_LABELS: Record<LinkedInSearch, string> = {
  api: "Without signing in, through LinkedIn's public job search",
  browser: "Signed in, in your own browser",
};

/** How a LinkedIn in the list is searched (owner, 2026-10-30: "Both, switchable in setup"): its
 * public job search, without an account (the default), or the browser agent, signed in. */
export function LinkedInSearchChoice({
  value,
  onChange,
}: {
  value: LinkedInSearch;
  onChange: (value: LinkedInSearch) => void;
}) {
  const id = useId();
  return (
    <FieldSet className="gap-1 pt-1 text-xs has-[>[data-slot=radio-group]]:gap-1">
      <FieldLegend
        variant="label"
        className="mb-1 data-[variant=label]:text-xs"
      >
        How LinkedIn is searched
      </FieldLegend>
      <RadioGroup
        value={value}
        onValueChange={(mode) => {
          onChange(mode as LinkedInSearch);
        }}
        className="gap-1"
      >
        {(["api", "browser"] as const).map((mode) => (
          // The radio hangs in the gutter, so its words start at the column's edge.
          <div key={mode} className={cn(HANG, "min-h-6 items-center")}>
            <span className="flex w-7 justify-center">
              <RadioGroupItem
                id={`${id}-${mode}`}
                value={mode}
                className="size-3.5"
              />
            </span>
            <Label
              htmlFor={`${id}-${mode}`}
              className="text-xs leading-4 font-normal"
            >
              {LINKEDIN_SEARCH_LABELS[mode]}
            </Label>
          </div>
        ))}
      </RadioGroup>
      <FieldDescription className="text-xs leading-4">
        Easy Apply always uses your browser.
      </FieldDescription>
    </FieldSet>
  );
}
