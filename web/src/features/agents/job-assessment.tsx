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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  LoaderCircle,
  Pencil,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { jobKey, jobsKey, type JobDetail } from "@/features/jobs/api";
import { statusLabel } from "@/features/jobs/labels";
import { describeError, errorDetail } from "@/lib/api";

import { getModelCredential, modelCredentialQueryKey } from "./api";
import {
  acceptProposal,
  conflictReason,
  jobProposalsKey,
  listJobProposals,
  rejectProposal,
  type Proposal,
} from "./proposals-api";
import { RunPanel } from "./run-panel";
import {
  FINAL_STATUSES,
  jobRunsKey,
  latestJobRun,
  runsKey,
  startRun,
  type RunStatus,
} from "./runs-api";

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

function ProposalCard({
  proposal,
  job,
}: {
  proposal: Proposal;
  job: JobDetail;
}) {
  const client = useQueryClient();
  const ids = { note: useId() };
  const isNote = proposal.kind === "job_note";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(payloadText(proposal, "text"));
  const refresh = () => {
    void client.invalidateQueries({ queryKey: jobProposalsKey(job.id) });
    void client.invalidateQueries({ queryKey: jobKey(job.id) });
    void client.invalidateQueries({ queryKey: [...jobsKey, "list"] });
  };
  const failed = (error: unknown) => {
    const reason = conflictReason(error);
    if (reason === "stale") {
      toast.error(
        "This job changed since it was loaded. It is reloaded now: check it and accept again.",
      );
    } else {
      toast.error(errorDetail(error) ?? describeError(error));
    }
    refresh();
  };
  const accept = useMutation({
    mutationFn: (edited?: string) =>
      acceptProposal(proposal.id, {
        updated_at: job.updated_at,
        ...(edited === undefined ? {} : { edited_value: edited }),
      }),
    onSuccess: () => {
      setEditing(false);
      toast.success(
        isNote
          ? "Note added to the job."
          : `Status changed to ${statusLabel(payloadText(proposal, "status"))}.`,
      );
      refresh();
    },
    onError: failed,
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(proposal.id),
    onSuccess: () => {
      toast.info("Proposal rejected.");
      refresh();
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
      className="flex flex-col gap-3 rounded-lg border p-4"
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
                variant="ghost"
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
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
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
        <LoaderCircle aria-hidden="true" className="animate-spin" />
      ) : (
        <Icon aria-hidden="true" />
      )}
      {label}
    </Button>
  );
}

export function JobAssessment({ job }: { job: JobDetail }) {
  const client = useQueryClient();
  const credential = useQuery({
    queryKey: modelCredentialQueryKey,
    queryFn: ({ signal }) => getModelCredential(signal),
  });
  const latest = useQuery({
    queryKey: jobRunsKey(job.id, TRIAGE),
    queryFn: ({ signal }) => latestJobRun(job.id, TRIAGE, signal),
    refetchInterval: (query) =>
      isActive(query.state.data?.status) ? POLL_MS : false,
  });
  const run = latest.data ?? null;
  const running = isActive(run?.status);
  const proposals = useQuery({
    queryKey: jobProposalsKey(job.id),
    queryFn: ({ signal }) => listJobProposals(job.id, signal),
    refetchInterval: running ? POLL_MS : false,
  });

  // Once the run ends, read its proposals one last time.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void client.invalidateQueries({ queryKey: jobProposalsKey(job.id) });
    }
    wasRunning.current = running;
  }, [running, client, job.id]);

  const start = useMutation({
    mutationFn: () => startRun({ agent: TRIAGE, job_id: job.id, input: {} }),
    onSuccess: (started) => {
      client.setQueryData(jobRunsKey(job.id, TRIAGE), started);
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    onError: (error) => {
      toast.error(
        `The assessment did not start. ${errorDetail(error) ?? describeError(error)}`,
      );
    },
  });

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
    <section
      aria-labelledby="assessment-title"
      className="flex flex-col gap-4 rounded-lg border p-4"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-col gap-1">
          <h2
            id="assessment-title"
            className="text-lg font-semibold tracking-tight"
          >
            Agent assessment
          </h2>
          <p className="text-sm text-muted-foreground">
            An agent reads this posting against your profile and proposes a note
            and a status. Nothing changes until you accept.
          </p>
        </div>
        <Button
          className="w-fit shrink-0"
          disabled={disabled}
          aria-busy={start.isPending}
          aria-describedby={noCredential ? "assessment-needs" : undefined}
          onClick={() => {
            start.mutate();
          }}
        >
          {start.isPending || running ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : (
            <Sparkles aria-hidden="true" />
          )}
          {running ? "Assessing" : "Assess with agent"}
        </Button>
      </div>
      {noCredential ? (
        <p id="assessment-needs" className="text-sm text-muted-foreground">
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
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">Proposals</h3>
          <ul aria-label="Proposals" className="flex flex-col gap-3">
            {[...pending, ...decided].map((proposal) => (
              <ProposalCard key={proposal.id} proposal={proposal} job={job} />
            ))}
          </ul>
        </div>
      ) : run && !running && proposals.isSuccess ? (
        <p className="text-sm text-muted-foreground">
          The last assessment proposed nothing.
        </p>
      ) : null}
    </section>
  );
}
