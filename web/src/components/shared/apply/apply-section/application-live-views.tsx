import type {
  Application,
  ApplicationEvent,
} from "@/features/apply/api/apply-api";
import type { Phase } from "@/features/apply/helpers/apply-state";
import {
  liveStatus,
  liveWait,
  liveWatchOnly,
} from "@/features/apply/helpers/live-view";
import { retrySentence } from "@/features/apply/helpers/needs-person-state";
import { LiveViewSlot } from "../live-view-slot";

function fillingSentence(direct: boolean, agentFill: boolean): string {
  if (direct)
    return "The assistant is filling the form. You can watch it live here, then it sends it.";
  if (agentFill)
    return "The assistant is filling the form. You can watch it live here, then you check what it filled and confirm.";
  return "The assistant is filling the form. A screenshot of the filled form appears here when it is done.";
}

function waitingSentence(
  application: Application | null,
  retrying: boolean,
  events: readonly ApplicationEvent[] | undefined,
): string {
  if (retrying && events) return retrySentence(events);
  return application
    ? liveWait(application)
    : "The browser starts soon. Then you can watch the assistant fill the form here.";
}

/**
 * The live view of the person's browser while the app works on an application: the wait before
 * the fill, then the lease the api names for the application, whatever its state (A23): the fill
 * with Use the browser myself, the submit lease and Review first's agent after the Confirm watch
 * only. Not for Apply myself, where the person's own browser panel stands in.
 */
export function ApplicationLiveViews({
  application,
  phase,
  direct,
  agentFill,
  retrying,
  events,
  submitLease,
}: {
  application: Application | null;
  phase: Phase;
  direct: boolean;
  agentFill: boolean;
  retrying: boolean;
  /** The events of the detail, once read: they say when the retry is due. */
  events: readonly ApplicationEvent[] | undefined;
  submitLease: { application: string } | null;
}) {
  const status = (purpose: string) => liveStatus(purpose, application);
  const watchOnly = (purpose: string) => liveWatchOnly(purpose, application);
  return (
    <>
      {phase === "filling" && application ? (
        <LiveViewSlot
          idle={fillingSentence(direct, agentFill)}
          leaseOf={{ application: application.id }}
          status={status}
          watchOnly={watchOnly}
        />
      ) : null}
      {phase === "waiting" ? (
        <LiveViewSlot
          idle={waitingSentence(application, retrying, events)}
          leaseOf="none"
        />
      ) : null}
      {submitLease && application ? (
        <LiveViewSlot
          idle={liveWait(application)}
          leaseOf={submitLease}
          status={status}
          watchOnly={watchOnly}
          stopped={application.state === "held"}
          namedOnly
        />
      ) : null}
    </>
  );
}
