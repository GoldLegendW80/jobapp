/**
 * Calls of the follow ups (SPEC-apply.md, "Follow ups"; api/app/apply/followups_router.py):
 * the list with its cadence, the switch, Write a draft, the person's own text, "I sent it" and
 * the Gmail draft. Every route reads and writes the signed in person only.
 */
import { z } from "zod";

import { request } from "@/lib/api";

const TIMEOUT_MS = 15_000;
/** The longest subject the api keeps (api/app/apply/followups.py, SUBJECT_MAX). */
export const SUBJECT_MAX = 200;
export const BODY_MAX = 20_000;

const issueSchema = z.object({
  code: z.string(),
  start: z.number().int(),
  end: z.number().int(),
});
export type FollowUpIssue = z.infer<typeof issueSchema>;

const draftSchema = z.object({
  subject: z.string(),
  body: z.string(),
  issues: z.array(issueSchema),
  written_by: z.enum(["agent", "person"]),
  writing: z.boolean(),
  error: z.string().nullable(),
  mail_draft_id: z.string().nullable(),
});
export type FollowUpDraft = z.infer<typeof draftSchema>;

const itemSchema = z.object({
  posting_id: z.string(),
  company: z.string(),
  title: z.string(),
  status: z.string(),
  applied_on: z.string(),
  applied_from: z.string(),
  kind: z.enum(["follow_up", "thank_you"]),
  urgency: z.enum(["due", "waiting", "cold", "answered", "done"]),
  next_on: z.string().nullable(),
  count: z.number().int(),
  sent: z.array(z.string()),
  can_draft: z.boolean(),
  draft: draftSchema.nullable(),
});
export type FollowUp = z.infer<typeof itemSchema>;

const listSchema = z.object({
  enabled: z.boolean(),
  today: z.string(),
  items: z.array(itemSchema),
});
export type FollowUps = z.infer<typeof listSchema>;

export const followUpsKey = ["apply-followups"] as const;

const path = (postingId: string, action: string) =>
  `/apply/followups/${encodeURIComponent(postingId)}/${action}`;

export const getFollowUps = (signal?: AbortSignal): Promise<FollowUps> =>
  request("/apply/followups", {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => listSchema.parse(answer),
  });

export const saveFollowUpsOn = (enabled: boolean) =>
  request("/apply/followups/settings", {
    method: "PUT",
    body: { enabled },
    validate: (answer) => z.object({ enabled: z.boolean() }).parse(answer),
  });

/** Write a draft: the person's click starts the follow up agent (202). */
export const writeFollowUp = (postingId: string): Promise<FollowUp> =>
  request(path(postingId, "draft"), {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => itemSchema.parse(answer),
  });

export const saveFollowUpText = (
  postingId: string,
  text: { subject: string; body: string },
): Promise<FollowUp> =>
  request(path(postingId, "draft"), {
    method: "PUT",
    body: text,
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => itemSchema.parse(answer),
  });

/** "I sent it": the follow up sent today from the person's own mail. */
export const recordFollowUpSent = (postingId: string): Promise<FollowUp> =>
  request(path(postingId, "sent"), {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => itemSchema.parse(answer),
  });

/** The draft as a Gmail draft to `to`: the person sends it with the mail gate's Send. */
export const followUpToGmail = (postingId: string, to: string) =>
  request(path(postingId, "gmail"), {
    method: "POST",
    body: { to },
    timeoutMs: TIMEOUT_MS,
    validate: (answer) => z.object({ draft_id: z.string() }).parse(answer),
  });
