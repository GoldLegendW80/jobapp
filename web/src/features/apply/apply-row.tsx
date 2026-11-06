/**
 * Apply on a row of the Jobs list (owner, 2026-11-05: "Apply also appears on Jobs list rows"),
 * offered under the posting page's conditions (no application under way, LinkedIn allowed, the
 * apply settings read) with the same reasons in a Tooltip while it waits. A misclick is easier on
 * a list, so the click opens a confirmation first (owner, 2026-11-06: "yes confirmation"): the
 * list's one dialog of Apply to a selection (`BulkApplyDialog`, opened by `onApply` with this
 * posting alone), which names the posting, its documents ready or written first, and what the
 * review box makes it do; its "Apply to 1" starts it as one Apply click. A row with an
 * application under way links to it instead.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { getLinkedIn, linkedinKey } from "@/features/browser/linkedin-api";
import type { JobListItem } from "@/features/jobs/api";

import {
  recentApplications,
  recentApplicationsKey,
  type Application,
} from "./apply-api";
import { applyDoesFromList, applyWaits, linkedInOf } from "./apply-offer";
import { phaseOf, pollInterval, type Phase } from "./apply-state";
import { applySettingsKey, getApplySettings } from "./settings-api";

/** What a row says once an application is under way, by its phase. */
const UNDER_WAY: Partial<Record<Phase, string>> = {
  waiting: "Applying",
  filling: "Applying",
  submitting: "Applying",
  needs: "Waiting for you",
  review: "Waiting for you",
  unknown: "Check it",
  refused: "Check it",
  submitted: "Sent",
};

/** The newest application of each posting, from the person's newest ones. */
function latestOf(items: Application[], postingId: string) {
  return items.find((item) => item.posting_id === postingId) ?? null;
}

export function ApplyRow({
  job,
  from,
  focusable,
  onApply,
}: {
  job: JobListItem;
  /** The list's query string, so the posting page can come back to the same view. */
  from: string;
  /** The row has the list's focus: its button is the next tab stop. */
  focusable: boolean;
  /** Opens the list's confirmation for this posting. */
  onApply: (postingId: string) => void;
}) {
  const recent = useQuery({
    queryKey: recentApplicationsKey,
    queryFn: ({ signal }) => recentApplications(signal),
    refetchInterval: (current) =>
      (current.state.data ?? []).some((item) => pollInterval(item) !== false)
        ? 5000
        : false,
  });
  const settings = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
  });
  const { onLinkedIn } = linkedInOf(job.url);
  const linkedin = useQuery({
    queryKey: linkedinKey,
    queryFn: ({ signal }) => getLinkedIn(signal),
    enabled: onLinkedIn,
  });
  const latest = recent.data ? latestOf(recent.data, job.id) : null;
  const phase = phaseOf(latest);
  if (recent.data && phase !== "idle") {
    const words = UNDER_WAY[phase];
    return words ? (
      <Link
        to={`/jobs/${job.id}`}
        state={{ from }}
        tabIndex={focusable ? 0 : -1}
        className="text-xs font-medium underline underline-offset-4"
      >
        {words}
        <span className="sr-only">
          : the application to {job.title} at {job.company}
        </span>
      </Link>
    ) : null;
  }
  // The posting page offers no Apply on LinkedIn until LinkedIn is read and allowed.
  if (onLinkedIn && !linkedin.data?.allowed) return null;

  const waits = recent.data
    ? applyWaits(settings)
    : recent.isError
      ? "Your applications could not be read. Open the posting to apply."
      : "Reading your applications";
  const disabled = waits !== null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* A disabled button gets no pointer events: the span carries the Tooltip. */}
        <span tabIndex={disabled && focusable ? 0 : -1} className="inline-flex">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 px-2.5 text-xs"
            disabled={disabled}
            tabIndex={focusable ? 0 : -1}
            aria-label={`Apply to ${job.title} at ${job.company}`}
            aria-haspopup="dialog"
            onClick={() => {
              onApply(job.id);
            }}
          >
            Apply
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs motion-reduce:animate-none!">
        {waits ?? (settings.data ? applyDoesFromList(settings.data) : "")}
      </TooltipContent>
    </Tooltip>
  );
}
