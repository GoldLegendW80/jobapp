import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useDocuments } from "@/features/ingestion/api/use-documents";
import { useStartRun } from "@/features/ingestion/api/use-start-run";
import {
  getRun,
  isActive,
  runKey,
  RUN_POLL_MS,
  useRuns,
  type Run,
} from "@/features/ingestion/api/runs-api";
import {
  acceptAdditions,
  getProposals,
} from "@/features/ingestion/api/proposals-api";
import { canBeChosen } from "@/features/ingestion/helpers/run-selection";
import {
  getProfile,
  profileKey,
  type Profile,
} from "@/features/profile/api/profile-api";

/** Where the building of the profile stands, as the step shows it. */
export type BuildPhase = "idle" | "reading" | "writing" | "ready" | "stopped";

/** How far a run is, 0 to 100: its steps that ended over all its steps. */
export function progressOf(run: Run | undefined): number {
  if (!run) return 0;
  if (run.status === "done") return 100;
  const { steps, done, reused, failed, cancelled } = run.counts;
  if (steps === 0) return 0;
  return Math.round(((done + reused + failed + cancelled) / steps) * 100);
}

/** A value the profile holds at a proposal's place: what accepting it would change. */
const holds = (value: unknown): boolean =>
  value !== null &&
  value !== undefined &&
  value !== "" &&
  !(Array.isArray(value) && value.length === 0) &&
  !(typeof value === "object" && Object.keys(value).length === 0);

/**
 * The wizard's "Building your profile" (owner, 2026-11-07): the reading of every file the person
 * added, started by them once they agree to what is sent, followed while it runs, then what adds
 * to the profile written at once, as "Accept all additions" would (asked, the owner chose "Write
 * the new ones at once"), with `fill_empty`: an empty place of the profile also takes a value two
 * files disagree on, or one the reading computed (2026-11-08). What would replace a value waits
 * in Fill my profile. A run already active when the step opens (a reload) is followed the same
 * way. `onWritten` gets the profile as it is after the writing, so the next steps show what was
 * found.
 */
export function useBuilding(onWritten: (profile: Profile) => void) {
  const client = useQueryClient();
  const documents = useDocuments();
  const ids = (documents.data?.items ?? [])
    .filter(canBeChosen)
    .map((one) => one.id);
  const [runId, setRunId] = useState<string>();
  const reading = useStartRun({
    documentIds: ids,
    onStarted: (run) => {
      setRunId(run.id);
    },
  });

  // A run already under way (a reload, another tab) is the one this step follows, kept once
  // adopted: it leaves the list's active run when it ends.
  const active = useRuns().data?.active_run_id ?? undefined;
  if (runId === undefined && active !== undefined) setRunId(active);

  const run = useQuery({
    queryKey: runKey(runId ?? "none"),
    queryFn: ({ signal }) => getRun(runId ?? "", signal),
    enabled: runId !== undefined,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === undefined || isActive(status) ? RUN_POLL_MS : false;
    },
  });

  const write = useMutation({
    mutationFn: async (id: string) => {
      const review = await getProposals(id);
      // Every pending item: the server takes what it may and leaves the rest with its reason.
      const pending = review.sections
        .flatMap((section) => section.items)
        .filter((item) => item.status === "pending");
      const done =
        pending.length > 0
          ? await acceptAdditions(
              id,
              pending.map((item) => item.id),
              { fillEmpty: true },
            )
          : null;
      const decided = new Set(done?.decided);
      const profile = await getProfile();
      client.setQueryData(profileKey, profile);
      return {
        written: done?.decided.length ?? 0,
        waiting: done?.pending ?? review.counts.pending,
        /** Of those left, the ones that would change a value the profile holds. */
        changing: pending.filter(
          (item) => !decided.has(item.id) && holds(item.before),
        ).length,
        profile,
      };
    },
    onSuccess: ({ profile }) => {
      onWritten(profile);
    },
  });

  const status = run.data?.status;
  const writeNow = write.mutate;
  const written = useRef<string>();
  // The run read everything it could: what it found is written, once per run.
  useEffect(() => {
    if (runId === undefined || status !== "done" || written.current === runId)
      return;
    written.current = runId;
    writeNow(runId);
  }, [runId, status, writeNow]);

  const phase: BuildPhase =
    runId === undefined
      ? "idle"
      : status === undefined || isActive(status)
        ? "reading"
        : status !== "done"
          ? "stopped"
          : write.isError
            ? "stopped"
            : write.isSuccess
              ? "ready"
              : "writing";

  return {
    count: ids.length,
    loading: documents.isPending,
    reading,
    run: run.data,
    phase,
    progress: progressOf(run.data),
    outcome: write.data,
    writeFailed: write.isError,
  };
}

export type Building = ReturnType<typeof useBuilding>;
