import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { jobKey, type JobDetail } from "@/features/jobs/api";
import { describeError } from "@/lib/api";

import {
  applicationKey,
  applicationsKey,
  approvalKey,
  approveApplication,
  cancelApplication,
  confirmSubmitted,
  getApplication,
  getApproval,
  latestApplication,
  refusalOf,
  rejectApplication,
  retryApplication,
  saveAnswer,
  startApplication,
  type Answer,
  type Application,
  type Approval,
} from "./apply-api";
import {
  blockingFields,
  documentLine,
  hasForm,
  hasReport,
  lastShot,
  phaseOf,
  pollInterval,
  statusLine,
} from "./apply-state";
import { ApproveDialog } from "./approve-dialog";
import { FieldsList } from "./fields-list";
import { LiveViewSlot } from "./live-view-slot";
import { RunReport } from "./run-report";
import { ShotView } from "./shot-view";

const STALE_NOTICE =
  "The form or your documents changed since you looked. Check the answers shown now and approve again.";

/**
 * The "Apply" section of the job page (SPEC-apply.md, "Frontend"): Apply with Review first, the
 * state of the fill, the answers and what is left for the person, the screenshots and documents,
 * then Approve and submit behind a confirmation. Nothing is sent without that approval; the page
 * only sends the hash of the form it showed, and the api refuses a form that moved. Everything
 * that matters lives on the server, so leaving the page and coming back shows the same state.
 */
export function ApplySection({ job }: { job: JobDetail }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [unsaved, setUnsaved] = useState(0);

  const latest = useQuery({
    queryKey: applicationsKey(job.id),
    queryFn: ({ signal }) => latestApplication(job.id, signal),
    refetchInterval: (current) => pollInterval(current.state.data),
  });
  const application = latest.data ?? null;
  const phase = phaseOf(application);
  const id = application?.id;

  const approvalQuery = useQuery({
    queryKey: approvalKey(id ?? ""),
    queryFn: ({ signal }) => getApproval(id as string, signal),
    enabled: id !== undefined && hasForm(application),
    refetchInterval: pollInterval(application),
  });
  const approval = approvalQuery.data;
  const detail = useQuery({
    queryKey: applicationKey(id ?? ""),
    queryFn: ({ signal }) => getApplication(id as string, signal),
    enabled: id !== undefined && hasReport(application),
  });

  // The state moved: read the form, the report and the posting (it may have become `applied`).
  const state = application?.state;
  const seen = useRef(state);
  useEffect(() => {
    if (seen.current === state) return;
    seen.current = state;
    if (id === undefined) return;
    void queryClient.invalidateQueries({ queryKey: approvalKey(id) });
    void queryClient.invalidateQueries({ queryKey: applicationKey(id) });
    void queryClient.invalidateQueries({ queryKey: jobKey(job.id) });
  }, [state, id, job.id, queryClient]);

  const settle = (changed?: Application) => {
    if (changed) {
      queryClient.setQueryData(applicationsKey(job.id), changed);
    }
    void queryClient.invalidateQueries({ queryKey: applicationsKey(job.id) });
    if (id !== undefined) {
      void queryClient.invalidateQueries({ queryKey: approvalKey(id) });
      void queryClient.invalidateQueries({ queryKey: applicationKey(id) });
    }
  };
  const failed = (error: unknown) => {
    setNotice(refusalOf(error)?.detail ?? describeError(error));
  };

  const start = useMutation({
    mutationFn: () => startApplication(job.id),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (started) => {
      settle(started);
    },
    onError: failed,
  });
  const decision = useMutation({
    mutationFn: (act: (applicationId: string) => Promise<Application>) =>
      act(id as string),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (changed) => {
      settle(changed);
    },
    onError: failed,
  });
  const save = useMutation({
    mutationFn: ({
      answer,
      value,
    }: {
      answer: Answer;
      value: string | boolean;
    }) => saveAnswer(id as string, answer.field_key, value),
    onMutate: () => {
      setNotice(null);
    },
    // The answer changes the form's hash: read the approval again before anything is approved.
    onSuccess: () => {
      settle();
    },
    onError: failed,
  });
  const approve = useMutation({
    mutationFn: (current: Approval) =>
      approveApplication(id as string, current.answers_hash),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (changed) => {
      setConfirming(false);
      settle(changed);
    },
    onError: (error) => {
      setConfirming(false);
      const refusal = refusalOf(error);
      if (
        refusal?.code === "stale" &&
        refusal.answers_hash &&
        refusal.answers
      ) {
        // Show the form as it is now; the old hash is never sent again.
        queryClient.setQueryData<Approval>(approvalKey(id as string), (old) =>
          old
            ? {
                ...old,
                answers_hash: refusal.answers_hash as string,
                answers: refusal.answers ?? old.answers,
              }
            : old,
        );
        setNotice(STALE_NOTICE);
        return;
      }
      failed(error);
    },
  });

  const busy =
    start.isPending ||
    decision.isPending ||
    approve.isPending ||
    save.isPending;
  const act = (call: (applicationId: string) => Promise<Application>) => {
    decision.mutate(call);
  };

  return (
    <section aria-labelledby="apply-title" className="flex flex-col gap-3">
      <h2 id="apply-title" className="text-lg font-semibold tracking-tight">
        Apply
      </h2>
      {latest.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the application
        </p>
      ) : latest.isError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-col gap-3">
            <p>
              The application could not be loaded. {describeError(latest.error)}
            </p>
            <Button
              variant="outline"
              className="w-full sm:w-fit"
              onClick={() => {
                void latest.refetch();
              }}
            >
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <p role="status" className="text-sm text-muted-foreground">
            {statusLine(application)}
          </p>
          {notice ? (
            <Alert variant="destructive">
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          ) : null}

          {phase === "waiting" || phase === "filling" ? (
            <LiveViewSlot waiting={phase === "waiting"} />
          ) : null}

          {application && hasForm(application) ? (
            approvalQuery.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  The filled form could not be loaded.{" "}
                  {describeError(approvalQuery.error)}
                </AlertDescription>
              </Alert>
            ) : approval ? (
              <FormPanel
                application={application}
                approval={approval}
                editable={phase === "review" || phase === "needs"}
                saving={save.isPending}
                onSave={async (answer, value) => {
                  try {
                    await save.mutateAsync({ answer, value });
                    return true;
                  } catch {
                    return false;
                  }
                }}
                onDirtyChange={setUnsaved}
              />
            ) : (
              <p
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <LoaderCircle
                  aria-hidden="true"
                  className="size-4 animate-spin"
                />
                Loading the filled form
              </p>
            )
          ) : null}

          {application && detail.data ? (
            <RunReport application={application} events={detail.data.events} />
          ) : null}

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {phase === "idle" ? (
              <>
                <Button
                  className="w-full sm:w-fit"
                  disabled={busy}
                  onClick={() => {
                    start.mutate();
                  }}
                >
                  {start.isPending ? (
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
                  ) : null}
                  Apply (Review first)
                </Button>
                <Button
                  variant="outline"
                  className="w-full sm:w-fit"
                  disabled
                  title="Coming soon"
                >
                  Apply myself (coming soon)
                </Button>
              </>
            ) : null}
            {phase === "review" && approval ? (
              <ReviewButtons
                job={job}
                approval={approval}
                busy={busy}
                unsaved={unsaved}
                onApprove={() => {
                  setConfirming(true);
                }}
                onReject={() => {
                  act(rejectApplication);
                }}
              />
            ) : null}
            {phase === "unknown" ? (
              <>
                <Button
                  className="w-full sm:w-fit"
                  disabled={busy}
                  onClick={() => {
                    act(confirmSubmitted);
                  }}
                >
                  It was submitted
                </Button>
                <Button
                  variant="outline"
                  className="w-full sm:w-fit"
                  disabled={busy}
                  onClick={() => {
                    act(retryApplication);
                  }}
                >
                  Try again
                </Button>
              </>
            ) : null}
            {phase === "refused" ? (
              <Button
                className="w-full sm:w-fit"
                disabled={busy}
                onClick={() => {
                  act(retryApplication);
                }}
              >
                Try again
              </Button>
            ) : null}
            {canCancel(application) ? (
              <Button
                variant="outline"
                className="w-full sm:w-fit"
                disabled={busy}
                onClick={() => {
                  act(cancelApplication);
                }}
              >
                Cancel application
              </Button>
            ) : null}
          </div>
          {phase === "idle" ? (
            <p className="text-sm text-muted-foreground">
              The assistant fills the form and stops. You look at every answer,
              then approve.
            </p>
          ) : null}

          {approval && application && phase === "review" ? (
            <ApproveDialog
              open={confirming}
              onOpenChange={setConfirming}
              company={job.company}
              role={job.title}
              approval={approval}
              pending={approve.isPending}
              onConfirm={() => {
                approve.mutate(approval);
              }}
            />
          ) : null}
        </>
      )}
    </section>
  );
}

/** Cancel is offered where the api accepts it and no click happened yet. */
function canCancel(application: Application | null): boolean {
  if (!application) return false;
  return ["queued", "filling", "needs_person", "approved", "held"].includes(
    application.state,
  );
}

function ReviewButtons({
  job,
  approval,
  busy,
  unsaved,
  onApprove,
  onReject,
}: {
  job: JobDetail;
  approval: Approval;
  busy: boolean;
  unsaved: number;
  onApprove: () => void;
  onReject: () => void;
}) {
  const blocking = blockingFields(approval.answers);
  return (
    <>
      <div className="flex w-full flex-col gap-1 sm:w-fit">
        <Button
          disabled={busy || blocking.length > 0 || unsaved > 0}
          onClick={onApprove}
        >
          Approve and submit
        </Button>
        {unsaved > 0 ? (
          <p className="text-sm text-amber-800 dark:text-amber-300">
            Save your answers first: only saved answers are sent.
          </p>
        ) : blocking.length > 0 ? (
          <p className="text-sm text-amber-800 dark:text-amber-300">
            Answer {blocking.map((answer) => answer.label).join(", ")} first:
            the form cannot go without it.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Sends your application to {job.company}.
          </p>
        )}
      </div>
      <Button
        variant="outline"
        className="w-full sm:w-fit"
        disabled={busy}
        onClick={onReject}
      >
        Reject
      </Button>
    </>
  );
}

function FormPanel({
  application,
  approval,
  editable,
  saving,
  onSave,
  onDirtyChange,
}: {
  application: Application;
  approval: Approval;
  editable: boolean;
  saving: boolean;
  onSave: (answer: Answer, value: string | boolean) => Promise<boolean>;
  onDirtyChange: (count: number) => void;
}) {
  const before = lastShot(approval, "before");
  const after = lastShot(approval, "after");
  const documents = Object.entries(approval.documents);
  return (
    <div className="flex flex-col gap-4">
      {approval.answers.length > 0 ? (
        <FieldsList
          answers={approval.answers}
          editable={editable}
          saving={saving}
          onSave={onSave}
          onDirtyChange={onDirtyChange}
        />
      ) : null}
      {documents.length > 0 ? (
        <section aria-label="Documents to send" className="flex flex-col gap-1">
          <h3 className="text-sm font-semibold">Documents sent with it</h3>
          <ul className="text-sm">
            {documents.map(([kind, one]) => (
              <li key={kind}>{documentLine(kind, one)}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {before || after ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {before ? (
            <ShotView
              applicationId={application.id}
              shotId={before.id}
              title="The form before the click"
            />
          ) : null}
          {after ? (
            <ShotView
              applicationId={application.id}
              shotId={after.id}
              title="The page after the click"
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
