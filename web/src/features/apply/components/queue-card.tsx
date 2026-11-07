import { TriangleAlert } from "lucide-react";
import { useId, useRef } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { LaterHandoffBanner } from "@/components/shared/browser/later-handoff-banner";
import { cn } from "@/lib/utils";

import type { QueueItem } from "../api/queue-api";
import { useQueueCardActions } from "../api/use-queue-card-actions";
import { useQueueCardAnswers } from "../api/use-queue-card-answers";
import { statusLine } from "../helpers/apply-state";
import { DECISIONS } from "../helpers/decisions";
import {
  isHeld,
  isReviewable,
  leftLine,
  reasonLine,
} from "../helpers/queue-state";
import { ApproveDialog } from "@/components/shared/apply/approve-dialog";
import { ConfirmDialog } from "@/components/shared/apply/confirm-dialog";
import { HeldCountdown } from "@/components/shared/apply/held-countdown";
import { FillHandoff } from "@/components/shared/apply/live-view-slot";
import { QueueCardActions } from "./queue-card/queue-card-actions";
import { QueueCardAnswers } from "./queue-card/queue-card-answers";
import { QueueCardDocuments } from "./queue-card/queue-card-documents";
import { RejectDialog } from "./queue-card/reject-dialog";
import { RehearsalQuestion } from "./rehearsal-question";
import { ShotView } from "@/components/shared/apply/shot-view";

const CANCEL = {
  title: "Cancel this application?",
  sentence: "Nothing is sent, and it leaves the queue.",
  confirm: "Cancel the application",
};

/**
 * One application of the review queue (SPEC-apply.md, "Frontend", `/applications`): the posting,
 * where the application stands, why it came back, then on wide screens the documents with their
 * check, what is left and the form before the click on one side, and the answers to read and
 * correct on the other, then its actions, the primary first. Approve goes through the same
 * gate route and confirmation as the posting's Apply section, with the hash of the form shown
 * here; Reject asks first and can also skip the posting. A Full auto item in its hold shows the
 * countdown with Cancel (`offset`: the server's clock minus the page's), and a rehearsal asks
 * "Does this look right?" over its answers, read only. After the click, an item the app could
 * not confirm or the site refused offers "It was submitted" and "Try again" with the posting
 * page's confirmations and calls. `onLeave` is called when the card is gone after a Reject, a
 * Cancel or an "It was submitted" of its own.
 */
export function QueueCard({
  item,
  onLeave,
  offset = 0,
  onUnsavedChange,
}: {
  item: QueueItem;
  onLeave: () => void;
  offset?: number;
  /** How many answers of this card are typed and not saved: Approve all ready leaves it out. */
  onUnsavedChange?: (id: string, count: number) => void;
}) {
  const titleId = useId();
  const article = useRef<HTMLElement>(null);
  const reviewable = isReviewable(item);
  const rehearsal = item.rehearsal === true;
  // Behind a sign in wall the answers wait for the fill after the sign in, which replaces them.
  const signingIn = item.state === "needs_person" && item.needs === "signin";
  // A20: the list of what the agent filled and its Confirm are on the job page.
  const agentList = item.review_agent === true;
  const withAnswers = (reviewable || rehearsal) && !signingIn && !agentList;

  const answers = useQueueCardAnswers(item, withAnswers, onUnsavedChange);
  const { approval, approvalQuery } = answers;
  const act = useQueueCardActions(item, {
    article,
    onLeave,
    onStaleForm: () => {
      answers.setOpen(true);
    },
  });
  const reason = reasonLine(item.reason);
  const left = leftLine(item);
  // The answers, and a rehearsal's question about them, take the wide column beside the facts.
  const aside = withAnswers || rehearsal;

  return (
    <article ref={article} aria-labelledby={titleId}>
      <Card className="gap-5">
        <CardHeader className="gap-1">
          <CardTitle>
            <h3
              id={titleId}
              tabIndex={-1}
              data-queue-card-title
              className="leading-snug outline-none"
            >
              <Link
                to={`/jobs/${item.posting_id}`}
                className="rounded-sm underline-offset-4 hover:underline"
              >
                {item.title}
              </Link>
            </h3>
          </CardTitle>
          <CardDescription>{item.company}</CardDescription>
        </CardHeader>

        <CardContent className="@container flex flex-col gap-4">
          <p className="text-sm">{statusLine(item, agentList)}</p>
          {item.state === "filling" && item.mode !== "manual" ? (
            <FillHandoff />
          ) : null}
          {item.mode === "manual" ? (
            <p className="text-sm">
              Open the posting to reopen your browser, record that you submitted
              it or close this application.
            </p>
          ) : null}
          {signingIn ? (
            <LaterHandoffBanner
              module="application"
              refId={item.id}
              address={item.form_url}
            />
          ) : null}
          {reason ? (
            <Alert variant="warning">
              <TriangleAlert aria-hidden="true" />
              <AlertDescription className="text-foreground">
                <p>
                  <span className="font-medium">Why it came back:</span>{" "}
                  {reason}
                </p>
              </AlertDescription>
            </Alert>
          ) : null}
          {act.notice ? (
            <Alert variant="destructive">
              <AlertDescription>{act.notice}</AlertDescription>
            </Alert>
          ) : null}

          <div
            className={cn(
              "grid gap-6",
              aside && "@3xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]",
            )}
          >
            <div
              className={cn(
                "flex min-w-0 flex-col gap-4",
                !aside && "max-w-sm",
              )}
            >
              <QueueCardDocuments item={item} />
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
              {item.shot_id ? (
                <ShotView
                  applicationId={item.id}
                  shotId={item.shot_id}
                  caption="The form before the click"
                />
              ) : null}
            </div>
            {aside ? (
              <div className="flex min-w-0 flex-col gap-4">
                {rehearsal ? (
                  <RehearsalQuestion
                    applicationId={item.id}
                    onLeave={act.markLeaving}
                  />
                ) : null}
                {withAnswers ? (
                  <QueueCardAnswers
                    applicationId={item.id}
                    open={answers.open}
                    onOpenChange={answers.setOpen}
                    approval={approval}
                    loadError={approvalQuery.error}
                    onRetry={() => void approvalQuery.refetch()}
                    editable={reviewable}
                    saving={act.save.isPending}
                    onSave={act.saveOne}
                    onDirtyChange={answers.setUnsaved}
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        </CardContent>

        <CardFooter className="flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap sm:items-start">
          <QueueCardActions
            item={item}
            reviewable={reviewable}
            agentList={agentList}
            approval={approval}
            approvalFetching={approvalQuery.isFetching}
            busy={act.busy}
            unsaved={answers.unsaved}
            onApprove={() => {
              act.setConfirming(true);
            }}
            onReject={() => {
              act.setRejecting(true);
            }}
            onCancel={() => {
              act.setCancelling(true);
            }}
            onAsk={act.setAsking}
          />
        </CardFooter>

        {approval && item.state === "ready" && reviewable ? (
          <ApproveDialog
            open={act.confirming}
            onOpenChange={act.setConfirming}
            company={item.company}
            role={item.title}
            approval={approval}
            pending={act.approve.isPending}
            onCloseAutoFocus={act.focusAfterApprove}
            onConfirm={(acknowledged = []) => {
              act.approve.mutate({ current: approval, acknowledged });
            }}
          />
        ) : null}
        {act.rejecting ? (
          <RejectDialog
            company={item.company}
            role={item.title}
            pending={act.reject.isPending}
            onCancel={() => {
              act.setRejecting(false);
            }}
            onConfirm={(skip) => {
              act.reject.mutate(skip);
            }}
          />
        ) : null}
        <ConfirmDialog
          ask={act.asking ? DECISIONS[act.asking] : null}
          pending={act.decision.isPending}
          onCancel={() => {
            act.setAsking(null);
          }}
          onConfirm={() => {
            if (act.asking) act.decision.mutate(act.asking);
          }}
        />
        <ConfirmDialog
          ask={act.cancelling ? CANCEL : null}
          pending={act.cancel.isPending}
          onCancel={() => {
            act.setCancelling(false);
          }}
          onConfirm={() => {
            act.cancel.mutate();
          }}
        />
      </Card>
    </article>
  );
}
