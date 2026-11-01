import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getLinkedIn, linkedinKey } from "@/features/browser/linkedin-api";
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
  APPLY_POLL_MS,
  blockingFields,
  decidedByApp,
  documentLine,
  hasForm,
  hasReport,
  lastShot,
  phaseOf,
  pollInterval,
  reviewReason,
  statusLine,
} from "./apply-state";
import { ApproveDialog } from "./approve-dialog";
import { ConfirmDialog, type Confirmation } from "./confirm-dialog";
import { EmailApplication } from "./email-application";
import { FieldsList } from "./fields-list";
import { serverOffset } from "./full-auto-state";
import { HeldCountdown } from "./held-countdown";
import { LiveViewSlot, type LeaseOf } from "./live-view-slot";
import { ManualPanel } from "./manual-panel";
import { NeedsPerson } from "./needs-person";
import { ON_PAGE, retrySentence, waitOf } from "./needs-person-state";
import { RunReport } from "./run-report";
import { applySettingsKey, getApplySettings } from "./settings-api";
import { ShotView } from "./shot-view";

/** The decisions that need a second click, each with its own sentence and the call it makes. */
const DECISIONS = {
  reject: {
    title: "Reject this filled form?",
    sentence: "Nothing is sent. You can start again.",
    confirm: "Reject the form",
    call: rejectApplication,
  },
  cancel: {
    title: "Cancel this application?",
    sentence: "Nothing is sent.",
    confirm: "Cancel the application",
    call: cancelApplication,
  },
  submitted: {
    title: "Mark this application as submitted?",
    sentence:
      "Only do this if you saw the confirmation. It cannot be undone, and the app will refuse another application for this posting.",
    confirm: "Mark as submitted",
    call: confirmSubmitted,
  },
  retry: {
    title: "Fill and approve again?",
    sentence:
      "If the first one went through, the company may receive two applications.",
    confirm: "Fill and approve again",
    call: retryApplication,
  },
} satisfies Record<
  string,
  Confirmation & { call: (applicationId: string) => Promise<Application> }
>;
type DecisionKind = keyof typeof DECISIONS;

/** A failed refresh keeps the section on screen after this many tries. */
const POLL_RETRIES = 2;
const POLL_RETRY_MS = 1000;

const STALE_NOTICE =
  "The form or your documents changed since you looked. Check the answers shown now and approve again.";

/** The submit lease in the live view, and the words before it starts. */
const SUBMIT_STATUS =
  "The assistant fills the form again with your approved answers, then submits it.";
const REHEARSAL_STATUS =
  "A rehearsal: the assistant fills the form again with your approved answers, then stops without submitting it.";
const SUBMIT_WAITS =
  "Waiting for your browser. The app spaces out submissions to the same site, so this one may wait its turn. Then you can watch the assistant fill the form again here.";
const HOLD_WAITS =
  "After the hold, you can watch the assistant fill the form again here.";
/** One click Apply's submit lease (mode `direct`): the click on Apply was the approval. */
const DIRECT_SUBMIT_STATUS =
  "The assistant fills the form again with the same answers, then sends it.";

/** What Apply does, by the person's "Let me review each filled form before it is sent" box. */
const APPLY_SENDS =
  "The assistant fills the form and sends it. You can watch it live here.";
const APPLY_REVIEWS =
  "The assistant fills the form, then you check it and send it.";

/**
 * The "Apply" section of the job page (SPEC-apply.md, "Frontend"; issue #738): one Apply button.
 * With the person's review box unticked (the default) the assistant fills the form and the app
 * sends it, the click on Apply being the approval, and the page follows it live; ticked, the fill
 * stops and the page shows the answers, what is left for the person, the screenshots and the
 * documents, then Approve and submit behind a confirmation, which only sends the hash of the
 * form it showed (the api refuses a form that moved). A form the app hands back to the person
 * says why. Everything that matters lives on the server, so leaving the page and coming back
 * shows the same state.
 */
export function ApplySection({ job }: { job: JobDetail }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [unsaved, setUnsaved] = useState(0);
  const [asking, setAsking] = useState<DecisionKind | null>(null);
  // A posting on LinkedIn starts nothing until the person has turned LinkedIn on and accepted its
  // current sentence (SPEC-browser decision 2); until then only the external link is offered.
  let onLinkedIn = false;
  let linkedinPosting: string | null = null;
  try {
    const posting = new URL(job.url ?? "");
    const host = posting.hostname.toLowerCase().replace(/\.$/, "");
    onLinkedIn = host === "linkedin.com" || host.endsWith(".linkedin.com");
    if (
      posting.protocol === "https:" &&
      !posting.username &&
      !posting.password &&
      ["linkedin.com", "www.linkedin.com"].includes(host) &&
      /^\/jobs\/view\/\d{1,24}\/?$/.test(posting.pathname) &&
      !posting.hash
    )
      linkedinPosting = posting.href;
  } catch {
    /* A posting without an address already has its normal refusal. */
  }
  const linkedin = useQuery({
    queryKey: linkedinKey,
    queryFn: ({ signal }) => getLinkedIn(signal),
    enabled: onLinkedIn,
  });
  const linkedinBlocked = onLinkedIn && !linkedin.data?.allowed;

  const latest = useQuery({
    queryKey: applicationsKey(job.id),
    queryFn: ({ signal }) => latestApplication(job.id, signal),
    refetchInterval: (current) => pollInterval(current.state.data),
    // A refresh that fails once is tried again; the first load is not, its error has a button.
    retry: (failures) =>
      queryClient.getQueryData(applicationsKey(job.id)) !== undefined &&
      failures < POLL_RETRIES,
    retryDelay: POLL_RETRY_MS,
  });
  const application = latest.data ?? null;
  const phase = phaseOf(application);
  const id = application?.id;
  const manual = application?.mode === "manual";
  const direct = application?.mode === "direct";
  const reason = reviewReason(application);
  // What Apply does is the person's setting; the line under the button says it once it is read.
  const settings = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
    enabled: phase === "idle",
  });

  const approvalQuery = useQuery({
    queryKey: approvalKey(id ?? ""),
    queryFn: ({ signal }) => getApproval(id as string, signal),
    enabled: id !== undefined && hasForm(application),
    refetchInterval: pollInterval(application),
    retry: (failures) =>
      queryClient.getQueryData(approvalKey(id ?? "")) !== undefined &&
      failures < POLL_RETRIES,
    retryDelay: POLL_RETRY_MS,
  });
  const approval = approvalQuery.data;
  // A step on the page: the run may wait for the person, and its events say until when.
  const onPage =
    application?.state === "needs_person" &&
    ON_PAGE.has(application.needs ?? "");
  const retrying =
    application?.state === "queued" && application.needs === "retry";
  const detail = useQuery({
    queryKey: applicationKey(id ?? ""),
    queryFn: ({ signal }) => getApplication(id as string, signal),
    enabled:
      id !== undefined &&
      (hasReport(application) ||
        application?.state === "held" ||
        onPage ||
        retrying),
    refetchInterval: (current) => {
      const events = current.state.data?.events;
      const wait = onPage && events ? waitOf(events) : null;
      return wait && !wait.over ? APPLY_POLL_MS : false;
    },
  });

  const holdUntil =
    application?.state === "held" ? detail.data?.hold_until : undefined;
  const submitLease = submitLeaseOf(application);

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

  // The person took the step in the live view and the run went on: follow it.
  const detailState = detail.data?.state;
  useEffect(() => {
    if (onPage && detailState !== undefined && detailState !== state) {
      void queryClient.invalidateQueries({
        queryKey: applicationsKey(job.id),
      });
    }
  }, [onPage, detailState, state, job.id, queryClient]);

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
  // Whatever the api refused or whatever timed out, the server may have moved on: show its state.
  const failed = (error: unknown) => {
    const refusal = refusalOf(error);
    setNotice(refusal?.detail ?? describeError(error));
    const state = refusal?.state;
    if (state) {
      queryClient.setQueryData<Application | null>(
        applicationsKey(job.id),
        (old) => (old && old.state !== state ? { ...old, state } : old),
      );
    }
    settle();
  };

  const start = useMutation({
    mutationFn: (mode: "apply" | "manual") => startApplication(job.id, mode),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (started) => {
      settle(started);
    },
    onError: failed,
  });
  const decision = useMutation({
    mutationFn: (kind: DecisionKind) => DECISIONS[kind].call(id as string),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (changed) => {
      setAsking(null);
      settle(changed);
    },
    onError: (error) => {
      setAsking(null);
      failed(error);
    },
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
    // The answer changes the form's hash: show the approval the api answered with, so Approve
    // never offers the old one, and read the state of the application again.
    onSuccess: (changed) => {
      queryClient.setQueryData(approvalKey(id as string), changed);
      void queryClient.invalidateQueries({ queryKey: applicationsKey(job.id) });
    },
    onError: failed,
  });
  const approve = useMutation({
    mutationFn: ({
      current,
      acknowledged,
    }: {
      current: Approval;
      acknowledged: string[];
    }) =>
      approveApplication(
        id as string,
        current.answers_hash,
        current.answers.find((answer) => answer.field_key === "email_to")
          ?.value as string | undefined,
        acknowledged,
      ),
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
        void queryClient.invalidateQueries({
          queryKey: applicationsKey(job.id),
        });
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
  const ask = (kind: DecisionKind) => {
    setAsking(kind);
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
      ) : latest.isError && latest.data === undefined ? (
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
          {reason ? (
            <Alert>
              <AlertDescription>
                Why it waits for you: {reason}
              </AlertDescription>
            </Alert>
          ) : null}
          {holdUntil && id ? (
            <HeldCountdown
              applicationId={id}
              until={holdUntil}
              offset={serverOffset(detail.data?.now, detail.dataUpdatedAt)}
            />
          ) : null}
          {latest.isError || approvalQuery.isError ? (
            <p role="status" className="text-sm text-muted-foreground">
              Could not refresh just now: this may be out of date. Reload the
              page if it stays so.
            </p>
          ) : null}
          {notice ? (
            <Alert variant="destructive">
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          ) : null}

          {application?.ats === "email" && id ? (
            <EmailApplication applicationId={id} />
          ) : null}
          {!manual ? (
            <NeedsPerson
              application={application}
              job={job}
              events={onPage ? detail.data?.events : undefined}
            />
          ) : null}
          {phase === "filling" && !manual ? (
            <LiveViewSlot
              idle={
                direct
                  ? "The assistant is filling the form. You can watch it live here, then it sends it."
                  : "The assistant is filling the form. A screenshot of the filled form appears here when it is done."
              }
              leaseOf="any"
            />
          ) : null}
          {phase === "waiting" && !manual ? (
            <LiveViewSlot
              idle={
                retrying
                  ? detail.data
                    ? retrySentence(detail.data.events)
                    : "The last attempt did not finish. The assistant tries again by itself."
                  : "The browser starts soon. A screenshot of the filled form appears here when it is done."
              }
              leaseOf="none"
            />
          ) : null}
          {submitLease && !manual ? (
            <LiveViewSlot
              idle={application?.state === "held" ? HOLD_WAITS : SUBMIT_WAITS}
              leaseOf={submitLease}
              status={
                application?.dry_run
                  ? REHEARSAL_STATUS
                  : direct
                    ? DIRECT_SUBMIT_STATUS
                    : SUBMIT_STATUS
              }
              stopped={application?.state === "held"}
              submit
            />
          ) : null}
          {application && manual ? (
            <ManualPanel
              application={application}
              onChanged={settle}
              onFailed={failed}
            />
          ) : null}

          {application &&
          application.needs !== "signin" &&
          hasForm(application) ? (
            approval ? (
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
            ) : approvalQuery.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  The filled form could not be loaded.{" "}
                  {describeError(approvalQuery.error)}
                </AlertDescription>
              </Alert>
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
                {linkedinBlocked ? (
                  <p className="text-sm text-muted-foreground">
                    Turn on LinkedIn in Account and accept its current sentence
                    to use Easy Apply, or tick LinkedIn in Find jobs and save.
                    You can also apply yourself.
                  </p>
                ) : (
                  <Button
                    className="w-full sm:w-fit"
                    disabled={busy}
                    onClick={() => {
                      start.mutate("apply");
                    }}
                  >
                    {start.isPending && start.variables === "apply" ? (
                      <LoaderCircle
                        aria-hidden="true"
                        className="animate-spin"
                      />
                    ) : null}
                    Apply
                  </Button>
                )}
                {linkedinBlocked ? (
                  linkedinPosting ? (
                    <Button
                      variant="outline"
                      className="w-full sm:w-fit"
                      asChild
                    >
                      <a
                        href={linkedinPosting}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open LinkedIn and apply myself
                      </a>
                    </Button>
                  ) : null
                ) : (
                  <Button
                    variant="outline"
                    className="w-full sm:w-fit"
                    disabled={busy}
                    onClick={() => {
                      start.mutate("manual");
                    }}
                  >
                    {start.isPending && start.variables === "manual" ? (
                      <LoaderCircle
                        aria-hidden="true"
                        className="animate-spin"
                      />
                    ) : null}
                    Apply myself
                  </Button>
                )}
              </>
            ) : null}
            {phase === "review" && approval ? (
              <ReviewButtons
                job={job}
                approval={approval}
                busy={busy || approvalQuery.isFetching}
                unsaved={unsaved}
                onApprove={() => {
                  setConfirming(true);
                }}
                onReject={() => {
                  ask("reject");
                }}
              />
            ) : null}
            {phase === "unknown" ? (
              <>
                <Button
                  className="w-full sm:w-fit"
                  disabled={busy}
                  onClick={() => {
                    ask("submitted");
                  }}
                >
                  It was submitted
                </Button>
                <Button
                  variant="outline"
                  className="w-full sm:w-fit"
                  disabled={busy}
                  onClick={() => {
                    ask("retry");
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
                  ask("retry");
                }}
              >
                Try again
              </Button>
            ) : null}
            {canCancel(application) && !manual && !holdUntil ? (
              <Button
                variant="outline"
                className="w-full sm:w-fit"
                disabled={busy}
                onClick={() => {
                  ask("cancel");
                }}
              >
                Cancel application
              </Button>
            ) : null}
          </div>
          {phase === "idle" && !linkedinBlocked ? (
            <div className="flex flex-col gap-1 text-sm text-muted-foreground">
              {settings.data ? (
                <p data-testid="apply-mode-line">
                  {settings.data.review_before_send
                    ? APPLY_REVIEWS
                    : APPLY_SENDS}{" "}
                  <Link
                    to="/settings/applications"
                    className="font-medium text-foreground underline underline-offset-4"
                  >
                    Change it in Settings
                  </Link>
                </p>
              ) : null}
              <p>
                With Apply myself, you fill and send the form yourself in your
                browser here.
              </p>
            </div>
          ) : null}

          <ConfirmDialog
            ask={asking ? DECISIONS[asking] : null}
            pending={decision.isPending}
            onCancel={() => {
              setAsking(null);
            }}
            onConfirm={() => {
              if (asking) decision.mutate(asking);
            }}
          />

          {approval && application && phase === "review" ? (
            <ApproveDialog
              open={confirming}
              onOpenChange={setConfirming}
              company={job.company}
              role={job.title}
              approval={approval}
              pending={approve.isPending}
              onConfirm={(acknowledged = []) => {
                approve.mutate({ current: approval, acknowledged });
              }}
            />
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * Cancel is offered where the api accepts it and no click happened yet, a Full auto or one click
 * Apply item the app still decides on included (a `ready` one in Review first has Reject instead).
 */
function canCancel(application: Application | null): boolean {
  if (!application) return false;
  if (application.state === "ready") return decidedByApp(application);
  return ["queued", "filling", "needs_person", "approved", "held"].includes(
    application.state,
  );
}

/**
 * This application's submit lease among the person's leases: the one the api names for it, as the
 * person may have several applications approved or held; null in a state without one.
 */
function submitLeaseOf(application: Application | null): LeaseOf | null {
  if (!application || application.ats === "email") return null;
  return ["approved", "held", "submitting"].includes(application.state)
    ? { application: application.id }
    : null;
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
