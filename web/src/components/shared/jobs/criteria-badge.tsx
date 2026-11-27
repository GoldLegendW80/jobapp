import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { useCriteriaWindow } from "@/features/jobs/api/use-criteria-window";
import {
  outsideCriteriaWords,
  type CriteriaFacts,
} from "@/features/jobs/helpers/criteria-words";

/**
 * Jobs J30: "Outside your criteria: freelance mission, remote, older than 30 days", on a posting
 * the search kept although it is outside a criterion of that search (the row of the Jobs list and
 * the posting's header). Nothing for a posting within them.
 */
export function CriteriaBadge({
  job,
  className,
}: {
  job: CriteriaFacts;
  className?: string;
}) {
  const windowDays = useCriteriaWindow(
    job.outside_criteria?.includes("older") ?? false,
  );
  const words = outsideCriteriaWords(job, windowDays);
  if (!words) return null;
  return (
    <Badge
      variant="info"
      data-testid="criteria-badge"
      className={cn("text-left font-normal whitespace-normal", className)}
    >
      {words}
    </Badge>
  );
}
