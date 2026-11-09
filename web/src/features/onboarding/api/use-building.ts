import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import { useMe } from "@/features/auth/api/use-me";
import { useDocuments } from "@/features/ingestion/api/use-documents";
import { useStartRun } from "@/features/ingestion/api/use-start-run";
import {
  getRun,
  isActive,
  runKey,
  RUN_POLL_MS,
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

/** The run this tab's wizard started and whether it was written: never one of Fill my profile. */
const startedKey = (userId: string) => `welcome:building:${userId}`;
const startedSchema = z.object({ run: z.string(), written: z.boolean() });
type Started = z.infer<typeof startedSchema>;

function readStarted(userId: string | undefined): Started | null {
  if (!userId) return null;
  try {
    const raw = window.sessionStorage.getItem(startedKey(userId));
    const parsed = startedSchema.safeParse(raw ? JSON.parse(raw) : null);
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

function keepStarted(userId: string | undefined, started: Started): void {
  if (!userId) return;
  try {
    window.sessionStorage.setItem(startedKey(userId), JSON.stringify(started));
  } catch {
    // Storage blocked: a reload offers the reading again instead of following it.
  }
}

/**
 * The wizard's "Building your profile" (owner, 2026-11-07): the reading of every file the person
 * added, started by them once they agree to what is sent, followed while it runs, then what adds
 * to the profile written at once, as "Accept all additions" would (asked, the owner chose "Write
 * the new ones at once"), with `fill_empty`: an empty place of the profile also takes a value two
 * files disagree on, or one the reading computed (2026-11-08). What would replace a value waits
 * in Fill my profile. The run this wizard started before the step opened (a reload, Back then
 * forward) is followed the same way, and written if it ended unwritten; no other run is (Fill my
 * profile keeps "nothing picks for the user", 2026-11-08). `onWritten` gets the profile as it is
 * after the writing, so the next steps show what was found.
 */
export function useBuilding(onWritten: (profile: Profile) => void) {
  const client = useQueryClient();
  const userId = useMe().data?.id;
  const documents = useDocuments();
  const ids = (documents.data?.items ?? [])
    .filter(canBeChosen)
    .map((one) => one.id);
  const [runId, setRunId] = useState<string>();
  const reading = useStartRun({
    documentIds: ids,
    startedFrom: "onboarding",
    onStarted: (run) => {
      keepStarted(userId, { run: run.id, written: false });
      setRunId(run.id);
    },
  });

  const started = useMemo(() => readStarted(userId), [userId]);
  const resumed = started && !started.written ? started.run : undefined;
  const followed = runId ?? resumed;
  const run = useQuery({
    queryKey: runKey(followed ?? "none"),
    queryFn: ({ signal }) => getRun(followed ?? "", signal),
    enabled: followed !== undefined,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (runId === undefined && status === undefined && query.state.error)
        return false;
      return status === undefined || isActive(status) ? RUN_POLL_MS : false;
    },
  });
  const status = run.data?.status;
  // A run resumed from an earlier visit that failed, was cancelled or cannot be read is let go.
  const current =
    runId ??
    (status !== undefined && (isActive(status) || status === "done")
      ? resumed
      : undefined);

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
      keepStarted(userId, { run: id, written: true });
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

  const writeNow = write.mutate;
  const written = useRef<string>();
  // The run read everything it could: what it found is written, once per run.
  useEffect(() => {
    if (
      current === undefined ||
      status !== "done" ||
      written.current === current
    )
      return;
    written.current = current;
    writeNow(current);
  }, [current, status, writeNow]);

  const phase: BuildPhase =
    current === undefined
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
    loading:
      documents.isPending ||
      (current === undefined && resumed !== undefined && run.isPending),
    reading,
    run: current === undefined ? undefined : run.data,
    phase,
    progress: progressOf(current === undefined ? undefined : run.data),
    outcome: write.data,
    writeFailed: write.isError,
  };
}

export type Building = ReturnType<typeof useBuilding>;
