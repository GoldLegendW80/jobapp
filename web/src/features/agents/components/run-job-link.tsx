import { Briefcase } from "lucide-react";
import { Link } from "react-router";

import type { Run } from "../api/runs-api";

/** The job a run worked on, as a link to it (A3); a deleted job is named as such. */
export function RunJobLink({ run }: { run: Run }) {
  if (run.job) {
    return (
      <Link
        to={`/jobs/${run.job.id}`}
        className="inline-flex w-fit min-w-0 items-center gap-1 text-sm underline underline-offset-4"
      >
        <Briefcase aria-hidden="true" className="size-3.5 shrink-0" />
        <span className="truncate">
          {run.job.title} at {run.job.company}
        </span>
      </Link>
    );
  }
  if (run.job_id) {
    return (
      <span className="text-sm text-muted-foreground">
        On a job that no longer exists
      </span>
    );
  }
  return null;
}
