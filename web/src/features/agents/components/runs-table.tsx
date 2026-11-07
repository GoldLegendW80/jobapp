import { Link } from "react-router";

import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime } from "@/features/jobs/helpers/format";
import { agentWord, runErrorWords } from "@/lib/wording";

import type { Run } from "../api/runs-api";
import {
  costQualifier,
  elapsed,
  formatCost,
  formatDuration,
  formatTokens,
} from "../helpers/format";
import { RunJobLink } from "./run-job-link";
import { RunStatusBadge } from "@/components/shared/agents/run/run-status-badge";

const COLUMNS = [
  "Run",
  "Started",
  "Status",
  "Posting",
  "Tokens",
  "Cost",
  "Duration",
] as const;

function Head() {
  return (
    <TableHeader>
      <TableRow>
        {COLUMNS.map((name) => (
          <TableHead
            key={name}
            className={
              name === "Tokens" || name === "Cost" || name === "Duration"
                ? "text-right"
                : undefined
            }
          >
            {name}
          </TableHead>
        ))}
      </TableRow>
    </TableHeader>
  );
}

/** The figures line up on the right from `sm`; on a phone each is a line of the run's card. */
const FIGURE = "whitespace-nowrap tabular-nums sm:text-right";

/** The runs, newest first: the whole row opens the run, the posting keeps its own link. */
export function RunsTable({ runs, now }: { runs: Run[]; now: number }) {
  return (
    <Table aria-label="Runs">
      <Head />
      <TableBody>
        {runs.map((run) => {
          const qualifier =
            run.cost_usd !== null ? costQualifier(run.credential_kind) : null;
          return (
            <TableRow key={run.id} className="relative">
              <TableHead className="min-w-48 font-normal">
                {/* Stretched over the row; the posting's link sits above it. */}
                <Link
                  to={`/runs/${run.id}`}
                  className="font-medium outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
                >
                  {agentWord(run.agent)}
                </Link>
                {run.error ? (
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                    {runErrorWords(run.error)}
                  </p>
                ) : null}
              </TableHead>
              <TableCell
                label="Started"
                className="whitespace-nowrap text-muted-foreground"
              >
                {formatDateTime(run.created_at)}
              </TableCell>
              <TableCell label="Status">
                <RunStatusBadge status={run.status} />
              </TableCell>
              <TableCell label="Posting" className="min-w-48">
                {run.job_id ? (
                  <span className="relative z-10 flex min-w-0">
                    <RunJobLink run={run} />
                  </span>
                ) : (
                  <span className="text-muted-foreground">None</span>
                )}
              </TableCell>
              <TableCell label="Tokens" className={FIGURE}>
                {formatTokens(run.input_tokens, run.output_tokens)}
              </TableCell>
              <TableCell label="Cost" className={FIGURE}>
                <span data-testid="run-cost">
                  {formatCost(run.cost_usd)}
                  {qualifier ? (
                    <span className="block text-xs text-muted-foreground">
                      {qualifier}
                    </span>
                  ) : null}
                </span>
              </TableCell>
              <TableCell label="Duration" className={FIGURE}>
                {formatDuration(elapsed(run, now))}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/** The table's place while the runs load: its header and a few grey rows. */
export function RunsTableLoading() {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">Loading the activity</span>
      <Table aria-hidden="true">
        <Head />
        <TableBody>
          {[0, 1, 2, 3].map((row) => (
            <TableRow key={row}>
              <TableHead className="min-w-48">
                <Skeleton className="h-4 w-36" />
              </TableHead>
              {COLUMNS.slice(1).map((name) => (
                <TableCell key={name}>
                  <Skeleton className="h-4 w-16" />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
