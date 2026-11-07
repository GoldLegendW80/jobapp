import { Link } from "react-router";

import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import type { AgentPage } from "../../api/agents-api";
import { whenStarted } from "@/features/admin/helpers/agent-labels";
import { AgentSection } from "./agent-section";

const PRIVATE =
  " A private agent's runs keep their numbers only, never what they read or wrote.";

const HEADS = [
  "Started",
  "Person",
  "Status",
  "Model",
  "Version",
  "Turns",
  "Tokens",
];

/** The agent's last runs, newest first; only the admin's own link to their run page. */
export function RecentRunsSection({ page }: { page: AgentPage }) {
  return (
    <AgentSection
      title="Recent runs"
      description={`Newest first. Only your own runs open on their page.${page.private_io ? PRIVATE : ""}`}
      surface="own"
    >
      {page.runs.length ? (
        <Table aria-label="Recent runs, newest first">
          <TableHeader>
            <TableRow>
              {HEADS.map((head) => (
                <TableHead key={head}>{head}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.runs.map((run) => (
              <TableRow key={run.id}>
                <TableHead className="font-normal">
                  {run.mine ? (
                    <Link
                      to={`/runs/${run.id}`}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      {whenStarted(run.started_at)}
                    </Link>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span>{whenStarted(run.started_at)}</span>
                      </TooltipTrigger>
                      <TooltipContent>
                        Another person's run: its page is theirs
                      </TooltipContent>
                    </Tooltip>
                  )}
                </TableHead>
                <TableCell label="Person">
                  <span className="flex min-w-0 flex-col">
                    <span className="break-words">
                      {run.user_name || run.user_email}
                    </span>
                    <span className="text-xs break-all text-muted-foreground">
                      {run.user_email}
                    </span>
                  </span>
                </TableCell>
                <TableCell label="Status">{run.status}</TableCell>
                <TableCell label="Model">{run.model ?? "Not known"}</TableCell>
                <TableCell label="Version">
                  {run.version === null
                    ? "The code's"
                    : `Version ${String(run.version)}`}
                </TableCell>
                <TableCell label="Turns">{run.turns ?? "Not known"}</TableCell>
                <TableCell label="Tokens">
                  {run.input_tokens === null && run.output_tokens === null
                    ? "Not known"
                    : `${String(run.input_tokens ?? 0)} in, ${String(run.output_tokens ?? 0)} out`}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            No run yet.
          </CardContent>
        </Card>
      )}
    </AgentSection>
  );
}
