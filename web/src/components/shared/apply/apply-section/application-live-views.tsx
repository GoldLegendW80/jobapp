import type {
  Application,
  ApplicationEvent,
} from "@/features/apply/api/apply-api";
import type { Phase } from "@/features/apply/helpers/apply-state";
import { retrySentence } from "@/features/apply/helpers/needs-person-state";
import { LiveViewSlot } from "../live-view-slot";

/** The submit lease in the live view, and the words before it starts. */
const SUBMIT_STATUS =
  "The assistant fills the form again with your approved answers, then submits it.";
const REHEARSAL_STATUS =
  "A rehearsal: the assistant fills the form again with your approved answers, then stops without submitting it.";
const SUBMIT_WAITS =
  "Waiting for your browser. The app spaces out submissions to the same site, so this one may wait its turn. Then you can watch the assistant fill the form again here.";
const HOLD_WAITS =
  "After the hold, you can watch the assistant fill the form again here.";
/** One click Apply's submit lease (mode `direct`): the click on Apply was the approval. */
const DIRECT_SUBMIT_STATUS =
  "The assistant fills the form again with the same answers, then sends it.";

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
  if (retrying)
    return events
      ? retrySentence(events)
      : "The last attempt did not finish. The assistant tries again by itself.";
  if (application?.needs?.startsWith("documents:"))
    return "The browser starts once your CV and letter are ready.";
  return "The browser starts soon. A screenshot of the filled form appears here when it is done.";
}

/**
 * The live view of the person's browser while the app works on an application: the fill, the wait
 * before it, and the submit lease once approved (or held, or submitting). Not for Apply myself,
 * where the person's own browser panel stands in.
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
  return (
    <>
      {phase === "filling" ? (
        <LiveViewSlot
          idle={fillingSentence(direct, agentFill)}
          leaseOf={application ? { application: application.id } : "none"}
        />
      ) : null}
      {phase === "waiting" ? (
        <LiveViewSlot
          idle={waitingSentence(application, retrying, events)}
          leaseOf="none"
        />
      ) : null}
      {submitLease ? (
        <LiveViewSlot
          idle={application?.state === "held" ? HOLD_WAITS : SUBMIT_WAITS}
          leaseOf={submitLease}
          status={
            application?.dry_run
              ? REHEARSAL_STATUS
              : direct
                ? DIRECT_SUBMIT_STATUS
                : SUBMIT_STATUS
          }
          stopped={application?.state === "held"}
          submit
        />
      ) : null}
    </>
  );
}
