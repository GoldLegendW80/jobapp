/**
 * The job triage agent on job detail (agents A3): "Assess with agent" starts a run of `triage`
 * on this job, the RunPanel shows it live, and what it proposes is listed inline with Accept,
 * Edit then accept (a note) and Reject. Nothing the agent proposes changes the job until it is
 * accepted; an accepted change shows in the job's history (the server writes the job event).
 *
 * Without a saved model credential the button is disabled and says why, with a link to the
 * Account page. The job's latest triage run is found again after a reload (GET
 * /api/agents/runs?job_id=&agent=triage), and the proposals are polled while it runs.
 */
import {
  Check,
  ClipboardCheck,
  Pencil,
  X,
  type LucideIcon,
} from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { statusLabel } from "@/features/jobs/helpers/labels";
import { describeError, errorDetail } from "@/lib/api";

import {
  conflictReason,
  type Proposal,
} from "@/features/agents/api/proposals-api";
import { RunPanel } from "./run-panel";
import { FINAL_STATUSES, type RunStatus } from "@/features/agents/api/runs-api";
import {
  useAcceptProposal,
  useJobProposals,
  useRejectProposal,
} from "@/features/agents/api/use-job-proposals";
import { useModelCredential } from "@/features/agents/api/use-model-credential";
import {
  useLatestJobRun,
  useStartJobRun,
} from "@/features/agents/api/use-runs";

export const TRIAGE = "triage";
const POLL_MS = 1500;
const NOTE_MAX = 4000;

const DECIDED_LABEL: Record<string, string> = {
  accepted: "Accepted",
  rejected: "Rejected",
  superseded: "Replaced by a newer one",
  invalid: "No longer applicable",
  undone: "Undone",
};

function isActive(status: RunStatus | undefined): boolean {
  return status !== undefined && !FINAL_STATUSES.has(status);
}

function payloadText(proposal: Proposal, key: string): string {
  const value = proposal.payload[key];
  return typeof value === "string" ? value : "";
}

function ProposalRow({
  proposal,
  job,
}: {
  proposal: Proposal;
  job: JobDetail;
}) {
  const ids = { note: useId() };
  const isNote = proposal.kind === "job_note";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(payloadText(proposal, "text"));
  const failed = (error: unknown) => {
    const reason = conflictReason(error);
    if (reason === "stale") {
      toast.error(
        "This job changed since it was loaded. It is reloaded now: check it and accept again.",
      );
    } else {
      toast.error(errorDetail(error) ?? describeError(error));
    }
  };
  const accept = useAcceptProposal(job, proposal.id, {
    onSuccess: () => {
      setEditing(false);
      toast.success(
        isNote
          ? "Note added to the job."
          : `Status changed to ${statusLabel(payloadText(proposal, "status"))}.`,
      );
    },
    onError: failed,
  });
  const reject = useRejectProposal(job.id, proposal.id, {
    onSuccess: () => {
      toast.info("Proposal rejected.");
    },
    onError: failed,
  });
  const busy = accept.isPending || reject.isPending;
  const pending = proposal.status === "pending";
  const status = payloadText(proposal, "status");
  const from = payloadText(proposal, "from");
  const title = isNote
    ? "Note"
    : `Status: ${from ? `${statusLabel(from)} to ` : ""}${statusLabel(status)}`;
  const trimmed = draft.trim();

  return (
    <li
      data-testid="proposal"
      data-kind={proposal.kind}
      data-status={proposal.status}
      className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{title}</h4>
        {pending ? (
          <Badge variant="secondary">Pending</Badge>
        ) : (
          <Badge variant="outline">
            {DECIDED_LABEL[proposal.status] ?? proposal.status}
          </Badge>
        )}
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <Label htmlFor={ids.note}>Edit the note</Label>
          <Textarea
            id={ids.note}
            value={draft}
            maxLength={NOTE_MAX}
            rows={6}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
        </div>
      ) : (
        <p className="text-sm break-words whitespace-pre-wrap text-muted-foreground">
          {isNote
            ? typeof proposal.edited_value === "string"
              ? proposal.edited_value
              : payloadText(proposal, "text")
            : payloadText(proposal, "reason")}
        </p>
      )}
      {pending ? (
        <div className="flex flex-wrap gap-2">
          {editing ? (
            <>
              <ActionButton
                icon={Check}
                label="Accept edited note"
                busy={accept.isPending}
                disabled={busy || !trimmed}
                onClick={() => {
                  accept.mutate(trimmed);
                }}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setDraft(payloadText(proposal, "text"));
                }}
              >
                Cancel
              </Button>
            </>
          ) : (
            <>
              <ActionButton
                icon={Check}
                label="Accept"
                busy={accept.isPending}
                disabled={busy}
                onClick={() => {
                  accept.mutate(undefined);
                }}
                aria-label={`Accept the ${isNote ? "note" : "status change"}`}
              />
              {isNote ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setEditing(true);
                  }}
                  aria-label="Edit the note, then accept"
                >
                  <Pencil aria-hidden="true" />
                  Edit
                </Button>
              ) : null}
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                aria-busy={reject.isPending}
                aria-label={`Reject the ${isNote ? "note" : "status change"}`}
                onClick={() => {
                  reject.mutate();
                }}
              >
                {reject.isPending ? (
                  <Spinner aria-hidden="true" role={undefined} />
                ) : (
                  <X aria-hidden="true" />
                )}
                Reject
              </Button>
            </>
          )}
        </div>
      ) : null}
    </li>
  );
}

function ActionButton({
  icon: Icon,
  label,
  busy,
  disabled,
  onClick,
  "aria-label": ariaLabel,
}: {
  icon: LucideIcon;
  label: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
  "aria-label"?: string;
}) {
  return (
    <Button
      size="sm"
      disabled={disabled}
      aria-busy={busy}
      aria-label={ariaLabel}
      onClick={onClick}
    >
      {busy ? (
        <Spinner aria-hidden="true" role={undefined} />
      ) : (
        <Icon aria-hidden="true" />
      )}
      {label}
    </Button>
  );
}

export function JobAssessment({ job }: { job: JobDetail }) {
  const credential = useModelCredential();
  const latest = useLatestJobRun(job.id, TRIAGE, POLL_MS);
  const run = latest.data ?? null;
  const running = isActive(run?.status);
  const proposals = useJobProposals(job.id, running, POLL_MS);

  const start = useStartJobRun(job.id, TRIAGE, {
    onError: (error) => {
      toast.error(
        `The assessment did not start. ${errorDetail(error) ?? describeError(error)}`,
      );
    },
  });

  const needsId = useId();
  const noCredential = credential.isSuccess && credential.data === null;
  const disabled =
    credential.isPending ||
    noCredential ||
    running ||
    start.isPending ||
    latest.isPending;
  const items = proposals.data ?? [];
  const pending = items.filter((p) => p.status === "pending");
  const decided = items.filter(
    (p) => p.status !== "pending" && run !== null && p.run_id === run.id,
  );

  return (
    <Section
      title="Agent assessment"
      description="An agent reads this posting against your profile and proposes a note and a status. Nothing changes until you accept."
      actions={
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          aria-busy={start.isPending}
          aria-describedby={noCredential ? needsId : undefined}
          onClick={() => {
            start.mutate();
          }}
        >
          {start.isPending || running ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : (
            <ClipboardCheck aria-hidden="true" />
          )}
          {running ? "Assessing" : "Assess with agent"}
        </Button>
      }
    >
      {noCredential ? (
        <p id={needsId} className="text-sm text-muted-foreground">
          Agents run with your own model credential, and none is saved yet.{" "}
          <Link to="/account" className="underline underline-offset-4">
            Add one on the Account page
          </Link>
          .
        </p>
      ) : null}
      {run ? (
        <RunPanel key={run.id} runId={run.id} title="Triage agent" />
      ) : null}
      {pending.length || decided.length ? (
        <Card>
          <CardContent className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Proposals</h3>
            <ul aria-label="Proposals" className="flex flex-col divide-y">
              {[...pending, ...decided].map((proposal) => (
                <ProposalRow key={proposal.id} proposal={proposal} job={job} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : run && !running && proposals.isSuccess ? (
        <p className="text-sm text-muted-foreground">
          The last assessment proposed nothing.
        </p>
      ) : null}
    </Section>
  );
}
