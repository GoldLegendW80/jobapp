/**
 * One run as the panel shows it: its detail (GET /api/agents/runs/{id}, refreshed when a step
 * changes its status or totals) and its steps, streamed live from the start (followRun), with the
 * state of the connection, and the text of the answer being written (`live`, live-text.ts).
 * Reloading the page or losing the network costs nothing: the stream replays what was missed.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  applyPartial,
  applyStep,
  emptyStream,
  type LiveText,
} from "./live-text";
import {
  FINAL_STATUSES,
  followRun,
  getRun,
  runKey,
  runsKey,
  type Connection,
  type RunStatus,
  type RunStep,
} from "./runs-api";

interface Followed {
  id: string;
  steps: RunStep[];
  live: LiveText | null;
  connection: Connection;
  gone: boolean;
}

export function useRunStream(id: string) {
  const client = useQueryClient();
  // Keyed by run id: switching to another run starts from an empty timeline without a reset.
  const [followed, setFollowed] = useState<Followed | null>(null);
  const detail = useQuery({
    queryKey: runKey(id),
    queryFn: ({ signal }) => getRun(id, signal),
  });

  useEffect(() => {
    const update = (change: (old: Followed) => Followed) => {
      setFollowed((old) =>
        change(
          old?.id === id
            ? old
            : { id, ...emptyStream, connection: "connecting", gone: false },
        ),
      );
    };
    return followRun(id, {
      onStep: (step) => {
        // followRun already drops repeats; applyStep also does, if a follow ever restarts
        // from the beginning for the same run (an effect run again). The stored text replaces
        // the live one in this same update: no frame shows both, or neither.
        update((old) => ({ ...old, ...applyStep(old, step) }));
        if (step.kind === "status" || step.kind === "usage") {
          void client.invalidateQueries({ queryKey: runKey(id) });
        }
        if (
          step.kind === "status" &&
          FINAL_STATUSES.has(step.data.status as RunStatus)
        ) {
          void client.invalidateQueries({ queryKey: runsKey, exact: true });
        }
      },
      onPartial: (part) => {
        update((old) => ({ ...old, ...applyPartial(old, part) }));
      },
      onConnection: (connection) => {
        update((old) => ({ ...old, connection }));
      },
      onGone: () => {
        update((old) => ({ ...old, gone: true }));
      },
    });
  }, [id, client]);

  const current = followed?.id === id ? followed : null;
  const steps = current?.steps ?? [];
  // The newest status step is ahead of the detail query until that query refetches.
  let status: RunStatus | undefined = detail.data?.status;
  for (const step of steps) {
    if (step.kind === "status" && typeof step.data.status === "string") {
      status = step.data.status as RunStatus;
    }
  }
  return {
    detail,
    steps,
    /** The answer being written, or null. Always a beginning of the text of its block. */
    live: current?.live ?? null,
    status,
    connection: current?.connection ?? "connecting",
    gone: current?.gone ?? false,
  };
}
