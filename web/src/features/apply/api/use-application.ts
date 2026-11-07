import {
  type QueryClient,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { getLinkedIn, linkedinKey } from "@/features/browser/api/linkedin-api";
import { jobKey, type JobDetail } from "@/features/jobs/api/jobs-api";

import {
  applicationKey,
  applicationsKey,
  approvalKey,
  getApplication,
  getApproval,
  latestApplication,
  type ApplicationDetail,
} from "./apply-api";
import { applySettingsKey, getApplySettings } from "./settings-api";
import { linkedInOf } from "../helpers/apply-offer";
import {
  APPLY_POLL_MS,
  hasForm,
  hasReport,
  phaseOf,
  pollInterval,
  reviewReason,
} from "../helpers/apply-state";
import { submitLeaseOf } from "../helpers/apply-section-rules";
import { serverOffset } from "../helpers/full-auto-state";
import { ON_PAGE, waitOf } from "../helpers/needs-person-state";

/** A failed refresh keeps the section on screen after this many tries. */
const POLL_RETRIES = 2;
const POLL_RETRY_MS = 1000;

/** The detail read says this application is Review first's apply agent's (A20). */
function reviewAgentOf(queryClient: QueryClient, applicationId: string) {
  const detail = queryClient.getQueryData<ApplicationDetail>(
    applicationKey(applicationId),
  );
  return detail?.review_agent === true;
}

/**
 * The latest application of a posting as the Apply section reads it: the application, the filled
 * form to approve, the detail (events, question, hold, agent list), the person's apply settings and
 * LinkedIn's permission, with what the section derives from them. Everything lives on the server,
 * so the reads poll while the app works and read again when the state moves.
 */
export function useApplication(job: JobDetail) {
  const queryClient = useQueryClient();
  // A posting on LinkedIn starts nothing until the person has turned LinkedIn on and accepted its
  // current sentence (SPEC-browser decision 2); until then only the external link is offered.
  const { onLinkedIn, posting: linkedinPosting } = linkedInOf(job.url);
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
  const direct = application?.mode === "direct";
  // What Apply does is the person's setting; the line under the button says it once it is read.
  const settings = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
    enabled: phase === "idle",
  });

  // Review first with the apply agent (A20): its list of filled fields and one Confirm, read from
  // the detail; a Review first item waits for the detail before the older approval view loads.
  const reviewReady =
    application?.mode === "review" && application.state === "ready";
  // A ticked Apply fills with the agent: no screenshot, its list comes after (A20).
  const reviewFilling = application?.mode === "review" && phase === "filling";
  const agentEnded =
    application?.state === "failed" &&
    (direct || application.mode === "review");
  const approvalQuery = useQuery({
    queryKey: approvalKey(id ?? ""),
    queryFn: ({ signal }) => getApproval(id as string, signal),
    enabled:
      id !== undefined &&
      hasForm(application) &&
      !reviewAgentOf(queryClient, id) &&
      !(reviewReady && !queryClient.getQueryData(applicationKey(id))),
    refetchInterval: pollInterval(application),
    retry: (failures) =>
      queryClient.getQueryData(approvalKey(id ?? "")) !== undefined &&
      failures < POLL_RETRIES,
    retryDelay: POLL_RETRY_MS,
  });
  // A step on the page: the run may wait for the person, and its events say until when.
  const onPage =
    application?.state === "needs_person" &&
    ON_PAGE.has(application.needs ?? "");
  const retrying =
    application?.state === "queued" && application.needs === "retry";
  // One click Apply's question (A19): the detail holds its sentence.
  const asked =
    application?.state === "needs_person" &&
    application.needs === "agent_question";
  const detail = useQuery({
    queryKey: applicationKey(id ?? ""),
    queryFn: ({ signal }) => getApplication(id as string, signal),
    enabled:
      id !== undefined &&
      (hasReport(application) ||
        reviewReady ||
        reviewFilling ||
        agentEnded ||
        application?.state === "held" ||
        onPage ||
        retrying ||
        asked),
    refetchInterval: (current) => {
      const events = current.state.data?.events;
      const wait = onPage && events ? waitOf(events) : null;
      return wait && !wait.over ? APPLY_POLL_MS : false;
    },
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

  // The person took the step in the live view and the run went on: follow it.
  const detailState = detail.data?.state;
  useEffect(() => {
    if (onPage && detailState !== undefined && detailState !== state) {
      void queryClient.invalidateQueries({
        queryKey: applicationsKey(job.id),
      });
    }
  }, [onPage, detailState, state, job.id, queryClient]);

  return {
    latest,
    application,
    phase,
    id,
    manual: application?.mode === "manual",
    direct,
    reason: reviewReason(application),
    settings,
    approvalQuery,
    approval: approvalQuery.data,
    detail,
    onPage,
    retrying,
    asked,
    agentEnded,
    agentFill: reviewFilling && detail.data?.review_agent === true,
    // A question asked after a control that may have sent the form (A19): its answer is the
    // person's Try again, in place of the plain button.
    afterSend:
      application?.state === "submit_unknown" &&
      detail.data?.question?.after_send
        ? detail.data.question
        : null,
    holdUntil:
      application?.state === "held" ? detail.data?.hold_until : undefined,
    holdOffset: serverOffset(detail.data?.now, detail.dataUpdatedAt),
    submitLease: submitLeaseOf(application),
    linkedinBlocked,
    linkedinPosting,
  };
}
