import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  isStale,
  jobKey,
  jobsKey,
  patchJob,
  type JobDetail,
} from "@/features/jobs/api";
import { describeError } from "@/lib/api";
import { LaterHandoffBanner } from "@/features/browser/later-handoff-banner";

import type { Application, ApplicationEvent } from "./apply-api";
import { LiveViewSlot } from "./live-view-slot";
import {
  getNeedsCount,
  needOf,
  needsCountKey,
  ON_PAGE,
  waitOf,
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
  events,
}: {
  application: Application | null;
  job: JobDetail;
  // The application's events; undefined while they load.
  events?: readonly ApplicationEvent[];
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
      {application.needs === "signin" ? (
        <LaterHandoffBanner
          module="application"
          refId={application.id}
          address={application.form_url}
          heading={false}
        />
      ) : null}
      {ON_PAGE.has(application.needs ?? "") ? (
        <OnPageSlot events={events} />
      ) : null}
    </div>
  );
}

const WAITS = "The assistant waits for you. Take over to finish this step.";

/** The live view while the run waits for the person on the page, and words once it stopped. */
function OnPageSlot({ events }: { events?: readonly ApplicationEvent[] }) {
  if (events === undefined) {
    return <LiveViewSlot idle={WAITS} leaseOf="none" />;
  }
  const wait = waitOf(events);
  if (wait === null || wait.over) {
    return (
      <LiveViewSlot
        idle="The assistant stopped waiting, so there is no live view. The application waits here for you."
        leaseOf="none"
        stopped
      />
    );
  }
  return (
    <LiveViewSlot
      idle={WAITS}
      leaseOf={{ startedBy: wait.since }}
      status={WAITS}
    />
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
    onError: (error) => {
      // Changed in another tab: read it again, so a second click carries its `updated_at`.
      if (isStale(error))
        void client.invalidateQueries({ queryKey: jobKey(job.id) });
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
