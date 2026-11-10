import { Link } from "react-router";
import { toast } from "sonner";

import type { WorkflowRunDetail } from "../api/activity-api";
import {
  NEW_POSTINGS_HREF,
  runHref,
  type RunEndWords,
} from "../helpers/run-ends";

const LINK =
  "rounded-sm font-medium text-foreground underline underline-offset-4 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** The toast's lines and links; a link closes the toast `id`. */
export function RunEndDetails({
  id,
  run,
  words,
}: {
  id: string;
  run: WorkflowRunDetail;
  words: RunEndWords;
}) {
  const close = () => {
    toast.dismiss(id);
  };
  return (
    <span className="flex flex-col gap-1">
      {words.lines.map((line) => (
        <span key={line} className="break-words">
          {line}
        </span>
      ))}
      <span className="flex flex-wrap gap-x-3 gap-y-1 pt-0.5">
        <Link to={runHref(run.id)} className={LINK} onClick={close}>
          Open the run
        </Link>
        {words.postings ? (
          <Link to={NEW_POSTINGS_HREF} className={LINK} onClick={close}>
            See the new postings
          </Link>
        ) : null}
      </span>
    </span>
  );
}
