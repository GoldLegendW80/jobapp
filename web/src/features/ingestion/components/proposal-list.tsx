import { Spinner } from "@/components/ui/spinner";
import { LoadError } from "@/components/blocks/load-error";
import { loadFailureSentence } from "@/features/ingestion/api/documents-api";

import { useFinishedRunId, useProposals } from "../api/use-proposals";
import { LoadedReview } from "./proposals/loaded-review";

/**
 * Step 3, third part: what a run proposes for each section of the profile, with the place in
 * the document each proposal comes from (task G12), and the decisions on them (task G13):
 * accept, edit then accept, reject, "Accept all in this section" and "Reject the rest of this
 * section". Nothing reaches the profile before a decision, and every decision is the server's:
 * the list is read again after each one, so what it shows is what was kept.
 *
 * What "accept all" leaves out (a locked fact, a flagged item, a removal, a replace, an item to
 * check again, one that would change a value the profile holds) keeps its badge or its line and
 * its own Accept button, and the section says how many were left, from the server's answer. An
 * accepted proposal can be taken back here ("Take back"), as from the History drawer.
 *
 * `runId` is the run to show. Without it the list finds the user's latest finished run by
 * itself, and renders nothing while no run has finished. The parts are in `proposals/`.
 */
export function ProposalList({ runId }: { runId?: string } = {}) {
  const finished = useFinishedRunId({ enabled: runId === undefined });
  const id = runId ?? finished.data ?? undefined;
  const query = useProposals(id);
  if (id === undefined) return null;
  // A read that fails after a decision keeps the list on screen: only a first read replaces it.
  if (query.data !== undefined) return <LoadedReview review={query.data} />;
  if (query.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading the proposals
      </p>
    );
  return (
    <LoadError query={query} reason={loadFailureSentence(query.error)}>
      The proposals could not be loaded.
    </LoadError>
  );
}
