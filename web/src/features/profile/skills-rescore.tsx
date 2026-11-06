/**
 * The two lines beside "Save skills" (product audit, chapter 2, owner answer 4 of 2026-11-05:
 * "Postings are rescored by themselves after a skills change, with the possible document cost
 * said before saving"). Since documents D29 nothing is written without a click, so a skills
 * change costs no document and no cost line is shown before the save. After a save,
 * `RescoreLine` follows the rescore the server runs by itself (`GET /api/jobs/rescore/status`)
 * and says what changed, with a link to the postings at the threshold. No dashes as separators.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { Spinner } from "@/components/ui/spinner";
import { getRescoreStatus, rescoreStatusKey } from "@/features/jobs/api";

/** How often the rescore after a save is read again while it runs. */
export const RESCORE_POLL_MS = 1500;

const postings = (count: number) =>
  count === 1 ? "1 posting" : `${String(count)} postings`;

/** Where the rescore after the save of `revision` stands, then what it changed. */
export function RescoreLine({ revision }: { revision: number }) {
  const status = useQuery({
    // Each save reads it afresh: an answer read before this save says nothing of it.
    queryKey: [...rescoreStatusKey, revision],
    queryFn: ({ signal }) => getRescoreStatus(signal),
    refetchInterval: (current) => {
      const data = current.state.data;
      if (!data) return RESCORE_POLL_MS;
      const asked =
        data.wanted_revision !== null && data.wanted_revision >= revision;
      return asked && data.done_revision < revision ? RESCORE_POLL_MS : false;
    },
  });
  const data = status.data;
  // This save changed no skill: no rescore was asked for, nothing to say.
  if (!data || data.wanted_revision === null || data.wanted_revision < revision)
    return null;
  if (data.done_revision < revision) {
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-muted-foreground"
        data-testid="rescore-line"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Rescoring your postings
      </p>
    );
  }
  const outcome = data.outcome;
  if (!outcome || outcome.no_skills || outcome.failed) {
    return (
      <p role="status" data-testid="rescore-line">
        Your postings were not rescored. Use Rescore all postings in Jobs.
      </p>
    );
  }
  const threshold = outcome.threshold ?? 70;
  const fewer =
    outcome.down > 0
      ? ` ${postings(outcome.down)} no longer ${outcome.down === 1 ? "does" : "do"}.`
      : "";
  return (
    <p role="status" data-testid="rescore-line">
      {outcome.up > 0
        ? `${outcome.up === 1 ? "1 more posting now fits" : `${String(outcome.up)} more postings now fit`} you at ${String(threshold)} or more.`
        : `Rescored: no new posting reaches a fit of ${String(threshold)}.`}
      {fewer}{" "}
      <Link
        to={`/jobs?fit_min=${String(threshold)}&sort=fit`}
        className="font-medium underline underline-offset-4"
      >
        See them in Jobs
      </Link>
    </p>
  );
}
