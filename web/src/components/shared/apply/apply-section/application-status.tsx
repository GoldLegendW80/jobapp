import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";

import type { Application } from "@/features/apply/api/apply-api";
import { documentsWaitLink } from "@/features/apply/helpers/apply-section-rules";
import { statusLine } from "@/features/apply/helpers/apply-state";
import { HeldCountdown } from "../held-countdown";

/**
 * Where the application stands, at the top of the Apply section: its status line, the page that
 * settles what its documents wait for, why it waits for the person, the hold of a Full auto item,
 * a refresh that failed, and the sentence of the last refusal.
 */
export function ApplicationStatus({
  application,
  reason,
  holdUntil,
  holdOffset,
  refreshFailed,
  notice,
}: {
  application: Application | null;
  reason: string | null;
  holdUntil: string | null | undefined;
  /** The server's clock minus the page's. */
  holdOffset: number;
  refreshFailed: boolean;
  notice: string | null;
}) {
  const waitLink = documentsWaitLink(application);
  return (
    <>
      <p role="status" className="text-sm text-muted-foreground">
        {statusLine(application)}
      </p>
      {waitLink ? (
        <Link
          to={waitLink.to}
          className="w-fit text-sm font-medium underline underline-offset-4"
        >
          {waitLink.label}
        </Link>
      ) : null}
      {reason ? (
        <Alert>
          <AlertDescription>Why it waits for you: {reason}</AlertDescription>
        </Alert>
      ) : null}
      {holdUntil && application ? (
        <HeldCountdown
          applicationId={application.id}
          until={holdUntil}
          offset={holdOffset}
        />
      ) : null}
      {refreshFailed ? (
        <p role="status" className="text-sm text-muted-foreground">
          Could not refresh just now: this may be out of date. Reload the page
          if it stays so.
        </p>
      ) : null}
      {notice ? (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}
    </>
  );
}
