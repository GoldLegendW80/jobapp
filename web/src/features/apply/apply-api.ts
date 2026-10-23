/**
 * Calls of the apply module that the job page's Apply section makes (SPEC-apply.md, "API"; the
 * routes of api/app/apply/router.py and approval_router.py). Bodies are checked with zod. States,
 * sources and event kinds are read as plain strings: a value a newer api adds still renders (with
 * a fallback label) instead of failing the whole section.
 */
import { z } from "zod";

import { API_BASE, isApiError, request } from "@/lib/api";

const TIMEOUT_MS = 15_000;

export const applicationSchema = z.object({
  id: z.string().min(1),
  posting_id: z.string().min(1),
  mode: z.string(),
  dry_run: z.boolean(),
  state: z.string(),
  ats: z.string(),
  form_url: z.string(),
  attempt: z.number().int(),
  needs: z.string().nullable(),
  error_code: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  submitted_at: z.string().nullable(),
});
export type Application = z.infer<typeof applicationSchema>;

const warningSchema = z.object({ code: z.string(), sentence: z.string() });
const startedSchema = applicationSchema.extend({
  warnings: z.array(warningSchema),
});
export type Started = z.infer<typeof startedSchema>;

const applicationListSchema = z.object({ items: z.array(applicationSchema) });

const eventSchema = z.object({
  at: z.string(),
  kind: z.string(),
  data: z.record(z.string(), z.unknown()),
});
export type ApplicationEvent = z.infer<typeof eventSchema>;

const detailSchema = applicationSchema.extend({
  events: z.array(eventSchema),
  // The end of a Full auto hold and the server's time, for the countdown.
  hold_until: z.string().nullable().optional(),
  now: z.string().optional(),
});
export type ApplicationDetail = z.infer<typeof detailSchema>;

const answerSchema = z.object({
  field_key: z.string(),
  label: z.string(),
  kind: z.string(),
  value: z.unknown(),
  source: z.string(),
  required: z.boolean(),
  approved: z.boolean(),
  you_answer: z.boolean(),
  max_chars: z
    .union([z.literal(998), z.literal(5000), z.literal(10000)])
    .optional(),
  left_reason: z.string().nullable().optional(),
  // The choices of a select field, when the api lists them.
  options: z.array(z.string()).nullable().optional(),
});
export type Answer = z.infer<typeof answerSchema>;

const approvalSchema = z.object({
  email: z
    .object({
      written_by: z.string(),
      issues: z.array(
        z.object({
          id: z.string(),
          code: z.string(),
          start: z.number(),
          end: z.number(),
        }),
      ),
    })
    .nullable()
    .optional(),
  state: z.string(),
  dry_run: z.boolean(),
  answers_hash: z.string(),
  answers: z.array(answerSchema),
  documents: z.record(
    z.string(),
    z.object({
      version_id: z.string().nullable().optional(),
      number: z.number().nullable().optional(),
      fit: z.unknown().optional(),
      pages: z.number().nullable().optional(),
      corrections: z.number().optional(),
    }),
  ),
  screenshots: z.array(
    z.object({ id: z.string(), kind: z.string(), created_at: z.string() }),
  ),
});
export type Approval = z.infer<typeof approvalSchema>;
export type ApprovalDocument = Approval["documents"][string];

/** The answer of a refusal: a sentence for the person, a code, and for `stale` the current form. */
const refusalSchema = z.object({
  detail: z.string(),
  code: z.string(),
  state: z.string().nullable().optional(),
  answers_hash: z.string().nullable().optional(),
  answers: z.array(answerSchema).nullable().optional(),
});
export type Refusal = z.infer<typeof refusalSchema>;

export const applicationsKey = (postingId: string) =>
  ["apply", "posting", postingId] as const;
export const applicationKey = (id: string) => ["apply", "detail", id] as const;
export const approvalKey = (id: string) => ["apply", "approval", id] as const;

const id = encodeURIComponent;

/** The newest application of a posting, or none. */
export const latestApplication = async (
  postingId: string,
  signal?: AbortSignal,
): Promise<Application | null> => {
  const list = await request(
    `/applications?posting_id=${id(postingId)}&limit=1`,
    {
      signal,
      timeoutMs: TIMEOUT_MS,
      validate: (body) => applicationListSchema.parse(body),
    },
  );
  return list.items[0] ?? null;
};

export const getApplication = (
  applicationId: string,
  signal?: AbortSignal,
): Promise<ApplicationDetail> =>
  request(`/applications/${id(applicationId)}`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => detailSchema.parse(body),
  });

export const getApproval = (
  applicationId: string,
  signal?: AbortSignal,
): Promise<Approval> =>
  request(`/applications/${id(applicationId)}/approval`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => approvalSchema.parse(body),
  });

/**
 * Apply with Review first (the agent fills the form and stops for the person's approval) or Apply
 * myself (the person fills and sends it in the live view, A9).
 */
export const startApplication = (
  postingId: string,
  mode: "review" | "manual" = "review",
): Promise<Started> =>
  request(`/postings/${id(postingId)}/applications`, {
    method: "POST",
    body: { mode },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => startedSchema.parse(body),
  });

const decide = (applicationId: string, action: string, body?: unknown) =>
  request(`/applications/${id(applicationId)}/${action}`, {
    method: "POST",
    body,
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => applicationSchema.parse(answer),
  });

/** Approve and submit exactly the form the hash covers. */
export const approveApplication = (
  applicationId: string,
  answersHash: string,
  confirmedEmailRecipient?: string,
  acknowledgedEmailIssues: string[] = [],
) =>
  decide(applicationId, "approve", {
    answers_hash: answersHash,
    ...(confirmedEmailRecipient
      ? {
          confirmed_email_recipient: confirmedEmailRecipient,
          acknowledged_email_issues: acknowledgedEmailIssues,
        }
      : {}),
  });
export const rejectApplication = (applicationId: string) =>
  decide(applicationId, "reject");
export const cancelApplication = (applicationId: string) =>
  decide(applicationId, "cancel");
/** "It was submitted": the person confirms what the app could not. */
export const confirmSubmitted = (applicationId: string) =>
  decide(applicationId, "submitted");
/** "Try again": back to the approval, nothing is sent without a new one. */
export const retryApplication = (applicationId: string) =>
  decide(applicationId, "retry");

/**
 * Type or correct one answer before approval, local to this application. The answer
 * changes the form's hash; the api answers with the approval view as it is now, new hash and
 * answers, which the page shows at once.
 */
export const saveAnswer = (
  applicationId: string,
  fieldKey: string,
  value: string | boolean,
): Promise<Approval> =>
  request(`/applications/${id(applicationId)}/answers/${id(fieldKey)}`, {
    method: "PUT",
    body: { value },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => approvalSchema.parse(body),
  });

/** The image is fetched by the browser with the session cookie. */
export const screenshotUrl = (applicationId: string, shotId: string) =>
  `${API_BASE}/applications/${id(applicationId)}/screenshots/${id(shotId)}`;

/** The refusal an api answered with (409), or undefined for any other error. */
export function refusalOf(error: unknown): Refusal | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const parsed = refusalSchema.safeParse(error.body);
  return parsed.success ? parsed.data : undefined;
}
