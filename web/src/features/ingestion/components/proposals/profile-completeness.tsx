import { useId } from "react";

import { Progress } from "@/components/ui/progress";

import { useCompleteness } from "../../api/use-proposals";

/** The completeness of the whole profile: it moves as proposals are accepted. */
export function ProfileCompleteness() {
  const label = useId();
  const query = useCompleteness();
  // The list works without it: when the profile cannot be read, the bar is left out.
  if (query.data === undefined) return null;
  const overall = query.data;
  return (
    <div className="flex flex-col gap-1.5" data-testid="review-completeness">
      <div className="flex items-baseline justify-between text-sm">
        <span id={label} className="font-medium">
          Profile completeness
        </span>
        <span className="text-muted-foreground tabular-nums">{overall}%</span>
      </div>
      {/* The value is passed to the bar's root too: shadcn's Progress keeps it for its fill. */}
      <Progress
        value={overall}
        aria-valuenow={overall}
        aria-labelledby={label}
      />
    </div>
  );
}
