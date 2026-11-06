import { Link } from "react-router";

import {
  Item,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
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

/** One run of the activity list: a link to it, and under it a link to its job. */
export function RunRow({ run, now }: { run: Run; now: number }) {
  // The job link sits under the run's link, never inside it.
  return (
    <li className="flex flex-col py-1">
      <Item
        asChild
        className="flex-col flex-nowrap items-stretch gap-2 rounded-lg sm:flex-row sm:items-center sm:gap-4"
      >
        <Link to={`/runs/${run.id}`}>
          <ItemContent className="min-w-0">
            <ItemTitle className="flex-wrap text-base leading-normal">
              {agentWord(run.agent)}
              <RunStatusBadge status={run.status} />
            </ItemTitle>
            <ItemDescription className="line-clamp-1 text-wrap">
              {formatDateTime(run.created_at)}
              {run.error ? `: ${runErrorWords(run.error)}` : ""}
            </ItemDescription>
          </ItemContent>
          <ItemContent className="grid grid-cols-3 gap-3 tabular-nums sm:w-96">
            <span>{formatTokens(run.input_tokens, run.output_tokens)}</span>
            <span data-testid="run-cost">
              {formatCost(run.cost_usd)}
              {run.cost_usd !== null && costQualifier(run.credential_kind) ? (
                <span className="block text-xs font-normal text-muted-foreground">
                  {costQualifier(run.credential_kind)}
                </span>
              ) : null}
            </span>
            <span>{formatDuration(elapsed(run, now))}</span>
          </ItemContent>
        </Link>
      </Item>
      {run.job_id ? (
        <span className="flex min-w-0 px-4 pb-3">
          <RunJobLink run={run} />
        </span>
      ) : null}
    </li>
  );
}
