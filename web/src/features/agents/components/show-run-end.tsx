import { toast, type ExternalToast } from "sonner";

import type { WorkflowRunDetail } from "../api/activity-api";
import { runEndWords } from "../helpers/run-ends";
import { RunEndDetails } from "./run-end-details";

// Long enough to read a failure's reason and reach a link.
export const RUN_END_TOAST_MS = 20_000;

/** The toast of a run that ended: success, error or plain by its status, one per run id. */
export function showRunEnd(
  run: WorkflowRunDetail,
  options: Pick<ExternalToast, "toasterId" | "duration"> = {},
) {
  const words = runEndWords(run);
  const id = `run-end:${run.id}`;
  const show =
    run.status === "success"
      ? toast.success
      : run.status === "failure"
        ? toast.error
        : toast.message;
  show(words.title, {
    id,
    duration: RUN_END_TOAST_MS,
    ...options,
    description: <RunEndDetails id={id} run={run} words={words} />,
  });
}
