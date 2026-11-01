import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { jobKey, jobsKey } from "@/features/jobs/api";
import { describeError } from "@/lib/api";

import {
  appliedMyself,
  applicationKey,
  applicationsKey,
  retryApplication,
  type Application,
} from "./apply-api";
import { canRetryStop, siteAddress } from "./needs-person-state";
import { queueKey } from "./queue-api";

/**
 * No dead ends (owner, 2026-11-01: "When I'm here, how can I continue applying ??"): on every
 * stop of an application (`needs_person`), on the job page and in the review queue, the person
 * can apply on the site in their own browser, then say "I applied" (recorded as sent, the posting
 * applied, no click by the app), or "Try again" where a new fill can help. Reject stays where
 * each page has it. No dashes as separators.
 */
export function StopActions({
  application,
  postingId,
  postingUrl,
  onDone,
}: {
  application: Application;
  postingId: string;
  postingUrl?: string | null;
  onDone?: (changed: Application, action: "applied" | "retry") => void;
}) {
  const client = useQueryClient();
  const address = siteAddress(application, postingUrl);
  const settle = () => {
    void client.invalidateQueries({ queryKey: applicationsKey(postingId) });
    void client.invalidateQueries({ queryKey: applicationKey(application.id) });
    void client.invalidateQueries({ queryKey: jobKey(postingId) });
    void client.invalidateQueries({ queryKey: jobsKey });
    void client.invalidateQueries({ queryKey: queueKey });
  };
  const act = useMutation({
    mutationFn: (action: "applied" | "retry") =>
      action === "applied"
        ? appliedMyself(application.id)
        : retryApplication(application.id),
    onSuccess: (changed, action) => {
      client.setQueryData(applicationsKey(postingId), changed);
      settle();
      onDone?.(changed, action);
    },
    onError: settle,
  });
  return (
    <div className="flex flex-col gap-2" data-testid="stop-actions">
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {address ? (
          <Button asChild className="w-full sm:w-fit">
            <a href={address} target="_blank" rel="noopener noreferrer">
              Apply on the site myself
              <ExternalLink aria-hidden="true" />
            </a>
          </Button>
        ) : null}
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={act.isPending}
          onClick={() => {
            act.mutate("applied");
          }}
        >
          I applied
        </Button>
        {canRetryStop(application) ? (
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={act.isPending}
            onClick={() => {
              act.mutate("retry");
            }}
          >
            Try again
          </Button>
        ) : null}
      </div>
      {act.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {describeError(act.error)}
        </p>
      ) : null}
    </div>
  );
}
