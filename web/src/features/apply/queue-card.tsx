import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { jobsKey } from "@/features/jobs/api";
import { describeError, errorDetail } from "@/lib/api";
import { LaterHandoffBanner } from "@/features/browser/later-handoff-banner";
import { cn } from "@/lib/utils";

import {
  applicationsKey,
  approvalKey,
  approveApplication,
  cancelApplication,
  getApproval,
  refusalOf,
  saveAnswer,
  type Answer,
  type Approval,
} from "./apply-api";
import { blockingFields, statusLine } from "./apply-state";
import { ApproveDialog } from "./approve-dialog";
import { ConfirmDialog } from "./confirm-dialog";
import { FieldsList } from "./fields-list";
import { HeldCountdown } from "./held-countdown";
import { FillHandoff } from "./live-view-slot";
import { queueKey, rejectQueued, type QueueItem } from "./queue-api";
import {
  canCancel,
  canReject,
  checkLine,
  isHeld,
  isReviewable,
  leftLine,
  needsLook,
  needsPostingPage,
  reasonLine,
} from "./queue-state";
import { RehearsalQuestion } from "./rehearsal-question";
import { ShotView } from "./shot-view";

const CANCEL = {
  title: "Cancel this application?",
  sentence: "Nothing is sent, and it leaves the queue.",
  confirm: "Cancel the application",
};

/**
 * One application of the review queue (SPEC-apply.md, "Frontend", `/applications`): the posting,
 * where the application stands, why it came back, the documents with their check, what is left,
 * the form before the click, and the answers to read and correct. Approve goes through the same
 * gate route and confirmation as the posting's Apply section, with the hash of the form shown
 * here; Reject asks first and can also skip the posting. A Full auto item in its hold shows the
 * countdown with Cancel (`offset`: the server's clock minus the page's), and a rehearsal asks
 * "Does this look right?" over its answers, read only. `onLeave` is called when the card is
 * gone after a Reject or a Cancel of its own.
 */
export function QueueCard({
  item,
  onLeave,
  offset = 0,
}: {
  item: QueueItem;
  onLeave: () => void;
  offset?: number;
}) {
  const queryClient = useQueryClient();
  const titleId = useId();
  const panelId = useId();
  const article = useRef<HTMLElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Until the person toggles it, the answers are open once a required one was seen left, and
  // stay open after it is answered: the person may still be on the optional ones.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const [requiredSeen, setRequiredSeen] = useState(item.required_left > 0);
  if (item.required_left > 0 && !requiredSeen) setRequiredSeen(true);
  const open = toggled ?? requiredSeen;
  const [unsaved, setUnsaved] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const reviewable = isReviewable(item);
  const rehearsal = item.rehearsal === true;
  // Behind a sign in wall the answers wait for the fill after the sign in, which replaces them.
  const signingIn = item.state === "needs_person" && item.needs === "signin";
  const withAnswers = (reviewable || rehearsal) && !signingIn;

  const approvalQuery = useQuery({
    queryKey: approvalKey(item.id),
    queryFn: ({ signal }) => getApproval(item.id, signal),
    enabled: withAnswers,
  });
  const approval = approvalQuery.data;

  // Set by a Reject or a Cancel that succeeded: the page takes the focus once the card is gone.
  const leaving = useRef(false);
  const focusAfterApproval = useRef(false);
  const approvalNextFocus = useRef<HTMLElement | null>(null);
  useEffect(
    () => () => {
      if (leaving.current) onLeave();
    },
    [onLeave],
  );

  // The state moved (the fill ended, another tab decided): read the form again.
  const seen = useRef(item.state);
  useEffect(() => {
    if (seen.current === item.state) return;
    seen.current = item.state;
    void queryClient.invalidateQueries({ queryKey: approvalKey(item.id) });
  }, [item.state, item.id, queryClient]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queueKey });
    void queryClient.invalidateQueries({
      queryKey: applicationsKey(item.posting_id),
    });
    void queryClient.invalidateQueries({ queryKey: approvalKey(item.id) });
  };
  const failed = (error: unknown) => {
    // The api's `detail` (a 409, or a 422 for an answer) is a sentence for the person, shown as is.
    setNotice(errorDetail(error) ?? describeError(error));
    refresh();
  };

  const save = useMutation({
    mutationFn: ({
      answer,
      value,
    }: {
      answer: Answer;
      value: string | boolean;
    }) => saveAnswer(item.id, answer.field_key, value),
    onMutate: () => {
      setNotice(null);
    },
    // The answer changes the form's hash: Approve only ever offers the form the api answered.
    onSuccess: (changed) => {
      queryClient.setQueryData(approvalKey(item.id), changed);
      void queryClient.invalidateQueries({ queryKey: queueKey });
    },
    onError: failed,
  });
  const approve = useMutation({
    mutationFn: (current: Approval) =>
      approveApplication(item.id, current.answers_hash),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: () => {
      const cards = Array.from(
        document.querySelectorAll<HTMLElement>("[data-queue-card-title]"),
      );
      const currentTitle = article.current?.querySelector<HTMLElement>(
        "[data-queue-card-title]",
      );
      approvalNextFocus.current = currentTitle
        ? (cards[cards.indexOf(currentTitle) + 1] ?? null)
        : null;
      setConfirming(false);
      refresh();
      focusAfterApproval.current = true;
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
        queryClient.setQueryData<Approval>(approvalKey(item.id), (old) =>
          old
            ? {
                ...old,
                answers_hash: refusal.answers_hash as string,
                answers: refusal.answers ?? old.answers,
              }
            : old,
        );
        setToggled(true);
        setNotice(refusal.detail);
        void queryClient.invalidateQueries({ queryKey: queueKey });
        return;
      }
      failed(error);
    },
  });
  const reject = useMutation({
    mutationFn: (skip: boolean) => rejectQueued(item.id, skip),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (rejected) => {
      setRejecting(false);
      toast.success(
        rejected.posting_status === "skipped"
          ? `Rejected ${item.title} at ${item.company}, and the posting is skipped. Nothing was sent.`
          : `Rejected ${item.title} at ${item.company}. Nothing was sent.`,
      );
      void queryClient.invalidateQueries({ queryKey: jobsKey });
      leaving.current = true;
      refresh();
    },
    onError: (error) => {
      setRejecting(false);
      failed(error);
    },
  });
  const cancel = useMutation({
    mutationFn: () => cancelApplication(item.id),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: () => {
      setCancelling(false);
      toast.success(
        `Cancelled ${item.title} at ${item.company}. Nothing was sent.`,
      );
      leaving.current = true;
      refresh();
    },
    onError: (error) => {
      setCancelling(false);
      failed(error);
    },
  });

  const busy =
    save.isPending || approve.isPending || reject.isPending || cancel.isPending;
  const blocking = approval ? blockingFields(approval.answers) : [];
  const reason = reasonLine(item.reason);
  const left = leftLine(item);
  const documents = Object.entries(item.documents);

  return (
    <article
      ref={article}
      aria-labelledby={titleId}
      className="flex flex-col gap-3 rounded-lg border bg-card p-4 text-card-foreground"
    >
      <header className="flex flex-col gap-0.5">
        <h3
          id={titleId}
          tabIndex={-1}
          data-queue-card-title
          className="leading-snug font-semibold outline-none"
        >
          <Link
            to={`/jobs/${item.posting_id}`}
            className="rounded-sm underline-offset-4 hover:underline"
          >
            {item.title}
          </Link>
        </h3>
        <p className="text-sm text-muted-foreground">{item.company}</p>
      </header>

      <p className="text-sm">{statusLine(item)}</p>
      {item.state === "filling" && item.mode !== "manual" ? (
        <FillHandoff />
      ) : null}
      {signingIn ? (
        <LaterHandoffBanner
          module="application"
          refId={item.id}
          address={item.form_url}
        />
      ) : null}
      {reason ? (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertDescription>
            <p>
              <span className="font-medium">Why it came back:</span> {reason}
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      {notice ? (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      <section aria-label="Documents" className="flex flex-col gap-1">
        <h4 className="text-sm font-semibold">Documents</h4>
        {item.documents_review ? (
          <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
            Review these documents before approval.
          </p>
        ) : null}
        {documents.length > 0 ? (
          <ul className="flex flex-col gap-0.5 text-sm">
            {documents.map(([kind, check]) => (
              <li
                key={kind}
                className={cn(
                  needsLook(check) && "text-amber-800 dark:text-amber-300",
                )}
              >
                <Link
                  to={`/jobs/${item.posting_id}/documents/${kind}`}
                  className="rounded-sm underline underline-offset-4"
                >
                  {checkLine(kind, check)}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            The documents show here once the form is filled.
          </p>
        )}
      </section>

      {left && !signingIn ? (
        <p className="text-sm font-medium">{left}</p>
      ) : null}

      {isHeld(item) && item.hold_until ? (
        <HeldCountdown
          applicationId={item.id}
          until={item.hold_until}
          offset={offset}
        />
      ) : null}
      {rehearsal ? (
        <RehearsalQuestion
          applicationId={item.id}
          onLeave={() => {
            leaving.current = true;
          }}
        />
      ) : null}

      {item.shot_id ? (
        <div className="sm:max-w-sm">
          <ShotView
            applicationId={item.id}
            shotId={item.shot_id}
            title="The form before the click"
          />
        </div>
      ) : null}

      {withAnswers ? (
        <div className="flex flex-col gap-3">
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => {
              setToggled(!open);
            }}
          >
            {open ? "Hide the answers" : "Review the answers"}
          </Button>
          <div id={panelId} hidden={!open}>
            {approval ? (
              <FieldsList
                answers={approval.answers}
                editable={reviewable}
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
                <AlertDescription className="flex flex-col items-start gap-2">
                  The answers could not be loaded.{" "}
                  {describeError(approvalQuery.error)}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void approvalQuery.refetch()}
                  >
                    Try again
                  </Button>
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
                Loading the answers
              </p>
            )}
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-start">
        {item.state === "ready" && reviewable ? (
          <div className="flex w-full flex-col gap-1 sm:w-fit">
            <Button
              disabled={
                busy ||
                !approval ||
                approvalQuery.isFetching ||
                blocking.length > 0 ||
                unsaved > 0
              }
              onClick={() => {
                setConfirming(true);
              }}
            >
              Approve and submit
            </Button>
            {unsaved > 0 ? (
              <p className="text-sm text-amber-800 dark:text-amber-300">
                Save your answers first: only saved answers are sent.
              </p>
            ) : blocking.length > 0 ? (
              <p className="text-sm text-amber-800 dark:text-amber-300">
                Answer {blocking.map((answer) => answer.label).join(", ")}{" "}
                first: the form cannot go without it.
              </p>
            ) : null}
          </div>
        ) : null}
        {canReject(item) ? (
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={busy}
            onClick={() => {
              setRejecting(true);
            }}
          >
            Reject
          </Button>
        ) : null}
        {canCancel(item) ? (
          <Button
            variant="outline"
            className="w-full sm:w-fit"
            disabled={busy}
            onClick={() => {
              setCancelling(true);
            }}
          >
            Cancel application
          </Button>
        ) : null}
        {needsPostingPage(item) ? (
          <Button asChild variant="outline" className="w-full sm:w-fit">
            <Link to={`/jobs/${item.posting_id}`}>Open the posting</Link>
          </Button>
        ) : null}
      </div>

      {approval && item.state === "ready" && reviewable ? (
        <ApproveDialog
          open={confirming}
          onOpenChange={setConfirming}
          company={item.company}
          role={item.title}
          approval={approval}
          pending={approve.isPending}
          onCloseAutoFocus={(event) => {
            if (!focusAfterApproval.current) return;
            focusAfterApproval.current = false;
            event.preventDefault();
            const next = approvalNextFocus.current;
            approvalNextFocus.current = null;
            if (next?.isConnected) next.focus();
            else onLeave();
          }}
          onConfirm={() => {
            approve.mutate(approval);
          }}
        />
      ) : null}
      {rejecting ? (
        <RejectDialog
          company={item.company}
          role={item.title}
          pending={reject.isPending}
          onCancel={() => {
            setRejecting(false);
          }}
          onConfirm={(skip) => {
            reject.mutate(skip);
          }}
        />
      ) : null}
      <ConfirmDialog
        ask={cancelling ? CANCEL : null}
        pending={cancel.isPending}
        onCancel={() => {
          setCancelling(false);
        }}
        onConfirm={() => {
          cancel.mutate();
        }}
      />
    </article>
  );
}

/** Reject asks first, opened fresh each time; "Skip this posting too" also sets the posting aside. */
function RejectDialog({
  company,
  role,
  pending,
  onCancel,
  onConfirm,
}: {
  company: string;
  role: string;
  pending: boolean;
  onCancel: () => void;
  onConfirm: (skip: boolean) => void;
}) {
  const boxId = useId();
  const [skip, setSkip] = useState(false);
  return (
    <AlertDialog
      open
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Reject the application to {company} for {role}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            Nothing is sent, and it leaves the queue. The posting stays in your
            jobs list unless you skip it.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex items-start gap-2">
          <input
            id={boxId}
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 accent-primary"
            checked={skip}
            disabled={pending}
            onChange={(event) => {
              setSkip(event.target.checked);
            }}
          />
          <Label htmlFor={boxId} className="leading-snug font-normal">
            Skip this posting too: it moves to Skipped in your jobs list.
          </Label>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Not yet</AlertDialogCancel>
          <Button
            onClick={() => {
              onConfirm(skip);
            }}
            disabled={pending}
          >
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Reject
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
