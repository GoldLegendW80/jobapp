/**
 * Saving changes of one job (PATCH /api/jobs/{id}) for the editors of job-editor.tsx: applied
 * to the cached job at once (optimistic), sent one after the other with the version last read
 * (so the api refuses a change based on an older version), the server's answer put in the cache
 * (the history updates with it), and the cached fields rolled back when the api refuses.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { describeError } from "@/lib/api";

import {
  isStale,
  jobKey,
  jobsKey,
  patchJob,
  type JobDetail,
  type JobPatch,
} from "./api";

/** Quiet time after the last keystroke before the notes are saved. */
export const NOTES_DEBOUNCE_MS = 800;
export const NOTES_MAX = 100_000;

export const STALE_MESSAGE =
  "This posting changed in another tab or window. The latest version is loaded: make your change again.";

/** Saves changes of one job: optimistic, one request at a time, rolled back on error. */
export function useJobEditor(id: string) {
  const client = useQueryClient();
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  return useCallback(
    (patch: JobPatch, optimistic?: Partial<JobDetail>): Promise<JobDetail> => {
      const key = jobKey(id);
      const before = client.getQueryData<JobDetail>(key);
      const restore: Partial<JobDetail> = {};
      if (before && optimistic) {
        for (const name of Object.keys(optimistic) as Array<keyof JobDetail>)
          (restore as Record<string, unknown>)[name] = before[name];
        client.setQueryData<JobDetail>(key, (old) =>
          old ? { ...old, ...optimistic } : old,
        );
      }
      const run = queue.current
        .catch(() => undefined)
        .then(() => {
          const current = client.getQueryData<JobDetail>(key);
          return patchJob(id, {
            ...patch,
            ...(current ? { updated_at: current.updated_at } : {}),
          });
        });
      queue.current = run;
      return run.then(
        (saved) => {
          client.setQueryData(key, saved);
          void client.invalidateQueries({ queryKey: [...jobsKey, "list"] });
          return saved;
        },
        (error: unknown) => {
          if (optimistic)
            client.setQueryData<JobDetail>(key, (old) =>
              old ? { ...old, ...restore } : old,
            );
          if (isStale(error)) void client.invalidateQueries({ queryKey: key });
          throw error;
        },
      );
    },
    [client, id],
  );
}

export function failure(error: unknown): string {
  if (isStale(error)) return STALE_MESSAGE;
  return `Not saved. ${describeError(error)}`;
}

export type Save = ReturnType<typeof useJobEditor>;
