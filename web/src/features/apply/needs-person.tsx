import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { jobKey, jobsKey, patchJob, type JobDetail } from "@/features/jobs/api";
import { describeError } from "@/lib/api";

import type { Application } from "./apply-api";
import { LiveViewSlot } from "./live-view-slot";
import {
  getNeedsCount,
  needOf,
  needsCountKey,
  ON_PAGE,
} from "./needs-person-state";
import { COUNT_POLL_MS } from "./queue-state";

/**
 * "Needs you" (SPEC-apply.md, "Failures and retries"; apply A10a): what an application waits for
 * the person for, on the job page, with the live view for a step on the page; the proposal to
 * mark a closed posting closed; and the app shell's dot, the count of the person's applications
 * that need them. No dashes as separators.
 */

/** On the job page: what the application needs from the person, or the closed posting's proposal. */
export function NeedsPerson({
  application,
  job,
}: {
  application: Application | null;
  job: JobDetail;
}) {
  if (application?.state === "posting_closed") {
    return <ClosedProposal job={job} />;
  }
  if (application?.state !== "needs_person") return null;
  const need = needOf(application);
  return (
    <div className="flex flex-col gap-3" data-testid="needs-person">
      <Alert>
        <AlertTitle>{need.title}</AlertTitle>
        <AlertDescription>{need.what}</AlertDescription>
      </Alert>
      {ON_PAGE.has(application.needs ?? "") ? (
        <LiveViewSlot
          waiting={false}
          status="The assistant waits for you. Take over to finish this step."
        />
      ) : null}
    </div>
  );
}

function ClosedProposal({ job }: { job: JobDetail }) {
  const client = useQueryClient();
  const close = useMutation({
    mutationFn: () =>
      patchJob(job.id, { status: "closed", updated_at: job.updated_at }),
    onSuccess: (updated) => {
      client.setQueryData(jobKey(job.id), updated);
      void client.invalidateQueries({ queryKey: jobsKey });
    },
  });
  if (job.status === "closed") return null;
  return (
    <Alert>
      <AlertTitle>This posting no longer takes applications</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <span>Move it to Closed so it leaves your list of open postings?</span>
        <Button
          className="w-full sm:w-fit"
          variant="outline"
          disabled={close.isPending}
          onClick={() => {
            close.mutate();
          }}
        >
          Mark it closed
        </Button>
        {close.isError ? (
          <span role="alert">{describeError(close.error)}</span>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

/** The app shell's dot: shown while at least one of the person's applications needs them. */
export function NeedsYouDot() {
  const query = useQuery({
    queryKey: needsCountKey,
    queryFn: ({ signal }) => getNeedsCount(signal),
    refetchInterval: COUNT_POLL_MS,
  });
  const count = query.data ?? 0;
  if (count <= 0) return null;
  return (
    <>
      <span
        aria-hidden="true"
        data-testid="needs-you-dot"
        className="ml-1 inline-block size-2 rounded-full bg-destructive"
      />
      <span className="sr-only">
        , {count} {count === 1 ? "needs" : "need"} you
      </span>
    </>
  );
}
