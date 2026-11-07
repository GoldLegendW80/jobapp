import { PageBody } from "@/components/blocks/page";
import { ProposalList } from "./proposal-list";
import { RunProgress } from "@/components/shared/ingestion/run-progress";
import { RunStart } from "@/components/shared/ingestion/run-start";

/**
 * Step 3, "Review": start a run, follow it, then decide on what it proposes. This file only
 * lays the three parts out, in the order the user meets them; each part lives in its own file
 * and fetches what it needs (tasks G11, G12 and G13 fill them in and do not edit this one).
 * Once a run has finished, its proposals take the main column and the run, with another
 * reading, the side rail.
 *
 * REVIEW_READY is what the page shell reads: while it is false the step shows as "Soon" and
 * cannot be opened. The task that makes a run startable from the page (G11) sets it to true.
 */
export const REVIEW_READY = true;

export function StepReview() {
  // One tree whatever the state, so the start keeps its ticks when a run ends. Until a run has
  // finished the proposals render nothing, their column hides, and the run takes its place.
  return (
    <PageBody
      className="empty:hidden"
      aside={
        <>
          <RunProgress />
          <RunStart />
        </>
      }
    >
      <ProposalList />
    </PageBody>
  );
}
