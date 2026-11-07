import { useState } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { JobDetail } from "@/features/jobs/api/jobs-api";

import { useApplication } from "@/features/apply/api/use-application";
import { useApplicationActions } from "@/features/apply/api/use-application-actions";
import {
  canCancelApplication,
  retryKindOf,
} from "@/features/apply/helpers/apply-section-rules";
import { hasForm } from "@/features/apply/helpers/apply-state";
import { DECISIONS } from "@/features/apply/helpers/decisions";
import { AgentFields, ChosenAnswers } from "./agent-fields";
import { AgentQuestion } from "./agent-question";
import { ApplicationLiveViews } from "./apply-section/application-live-views";
import { ApplicationStatus } from "./apply-section/application-status";
import { ApplyModeLine } from "./apply-section/apply-mode-line";
import { DecisionButtons } from "./apply-section/decision-buttons";
import { FilledForm } from "./apply-section/filled-form";
import { ReviewButtons } from "./apply-section/review-buttons";
import { StartButtons } from "./apply-section/start-buttons";
import { ApproveDialog } from "./approve-dialog";
import { ConfirmDialog } from "./confirm-dialog";
import { EmailApplication } from "./email-application";
import { ManualPanel } from "./manual-panel";
import { NeedsPerson } from "./needs-person";
import { RunReport } from "./run-report";

/**
 * "Your application" on the job page (SPEC-apply.md, "Frontend"; issue #738): one Apply button.
 * With the person's review box unticked (the default) the assistant fills the form and the app
 * sends it, the click on Apply being the approval, and the page follows it live; ticked, the fill
 * stops and the page shows the answers, what is left for the person, the screenshots and the
 * documents, then Approve and submit behind a confirmation, which only sends the hash of the
 * form it showed (the api refuses a form that moved). A form the app hands back to the person
 * says why. Everything that matters lives on the server, so leaving the page and coming back
 * shows the same state.
 */
export function ApplySection({ job }: { job: JobDetail }) {
  const [unsaved, setUnsaved] = useState(0);
  const read = useApplication(job);
  const { application, phase, id, manual, approval, approvalQuery, detail } =
    read;
  const act = useApplicationActions(job.id, id);

  return (
    <Section title="Your application">
      {read.latest.isError && read.latest.data === undefined ? (
        <LoadError query={read.latest}>
          The application could not be loaded.
        </LoadError>
      ) : (
        <Card>
          {/* Its buttons stack while it is narrow, in the side rail or on a phone. */}
          <CardContent className="@container flex flex-col gap-4">
            {read.latest.isPending ? (
              <p
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Spinner aria-hidden="true" role={undefined} />
                Loading the application
              </p>
            ) : (
              <>
                <ApplicationStatus
                  application={application}
                  reason={read.reason}
                  holdUntil={read.holdUntil}
                  holdOffset={read.holdOffset}
                  refreshFailed={read.latest.isError || approvalQuery.isError}
                  notice={act.notice}
                  reviewAgent={detail.data?.review_agent === true}
                />

                {application?.ats === "email" && id ? (
                  <EmailApplication applicationId={id} />
                ) : null}
                {!manual ? (
                  <NeedsPerson
                    application={application}
                    job={job}
                    events={read.onPage ? detail.data?.events : undefined}
                    question={read.asked ? detail.data?.question : undefined}
                  />
                ) : null}
                {application && read.afterSend && !manual ? (
                  <AgentQuestion
                    application={application}
                    question={read.afterSend}
                  />
                ) : null}
                {application &&
                detail.data?.review_agent &&
                detail.data.filled ? (
                  <AgentFields
                    application={application}
                    filled={detail.data.filled}
                    onChanged={act.settle}
                  />
                ) : null}
                {application && read.agentEnded && detail.data?.summary ? (
                  <p className="text-sm">{detail.data.summary}</p>
                ) : null}
                {application && !manual && detail.data?.chosen?.length ? (
                  <ChosenAnswers chosen={detail.data.chosen} />
                ) : null}
                {!manual ? (
                  <ApplicationLiveViews
                    application={application}
                    phase={phase}
                    direct={read.direct}
                    agentFill={read.agentFill}
                    retrying={read.retrying}
                    events={detail.data?.events}
                    submitLease={read.submitLease}
                  />
                ) : null}
                {application && manual ? (
                  <ManualPanel
                    application={application}
                    onChanged={act.settle}
                    onFailed={act.failed}
                  />
                ) : null}

                {/* The apply agent's list (AgentFields) replaces the filled form, which is never read. */}
                {application &&
                application.needs !== "signin" &&
                hasForm(application) &&
                !detail.data?.review_agent ? (
                  <FilledForm
                    application={application}
                    approval={approval}
                    loadError={approvalQuery.error}
                    editable={phase === "review" || phase === "needs"}
                    saving={act.save.isPending}
                    onSave={act.saveOne}
                    onDirtyChange={setUnsaved}
                  />
                ) : null}

                {application && detail.data ? (
                  <RunReport
                    application={application}
                    events={detail.data.events}
                  />
                ) : null}

                <div className="flex flex-col gap-2 @sm:flex-row @sm:flex-wrap">
                  {phase === "idle" ? (
                    <StartButtons
                      busy={act.busy}
                      settingsRead={read.settings.data !== undefined}
                      starting={
                        act.start.isPending ? act.start.variables : null
                      }
                      linkedinBlocked={read.linkedinBlocked}
                      linkedinPosting={read.linkedinPosting}
                      onStart={act.start.mutate}
                    />
                  ) : null}
                  {phase === "review" && approval ? (
                    <ReviewButtons
                      job={job}
                      approval={approval}
                      busy={act.busy || approvalQuery.isFetching}
                      unsaved={unsaved}
                      onApprove={() => {
                        act.setConfirming(true);
                      }}
                      onReject={() => {
                        act.setAsking("reject");
                      }}
                    />
                  ) : null}
                  <DecisionButtons
                    phase={phase}
                    busy={act.busy}
                    questionAfterSend={read.afterSend !== null}
                    retryKind={retryKindOf(detail.data)}
                    cancellable={
                      canCancelApplication(application) &&
                      !manual &&
                      !read.holdUntil
                    }
                    onAsk={act.setAsking}
                  />
                </div>
                {phase === "idle" && !read.linkedinBlocked ? (
                  <ApplyModeLine
                    settings={read.settings.data}
                    failed={read.settings.isError}
                    onRetry={() => {
                      void read.settings.refetch();
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

                {approval && application && phase === "review" ? (
                  <ApproveDialog
                    open={act.confirming}
                    onOpenChange={act.setConfirming}
                    company={job.company}
                    role={job.title}
                    approval={approval}
                    pending={act.approve.isPending}
                    onConfirm={(acknowledged = []) => {
                      act.approve.mutate({ current: approval, acknowledged });
                    }}
                  />
                ) : null}
              </>
            )}
          </CardContent>
        </Card>
      )}
    </Section>
  );
}
