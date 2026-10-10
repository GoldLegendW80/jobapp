import { ProposalList } from "./proposal-list";
import { RunProgress } from "./run-progress";
import { RunStart } from "./run-start";

/**
 * Step 3, "Review": start a run, follow it, then decide on what it proposes. This file only
 * lays the three parts out, in the order the user meets them; each part lives in its own file
 * and fetches what it needs (tasks G11, G12 and G13 fill them in and do not edit this one).
 *
 * REVIEW_READY is what the page shell reads: while it is false the step shows as "Soon" and
 * cannot be opened. The task that makes a run startable from the page (G11) sets it to true.
 */
export const REVIEW_READY = false;

export function StepReview() {
  return (
    <div className="flex flex-col gap-6">
      <RunStart />
      <RunProgress />
      <ProposalList />
    </div>
  );
}
