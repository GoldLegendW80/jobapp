/**
 * The interview preparation of a posting (SPEC-documents.md, "Interview preparation"): its read,
 * Prepare, the Practice chat, the stories kept for the profile, and the hand save that appends a
 * debrief. Shapes mirror api/app/documents/interview_prep_router.py.
 */
import { z } from "zod";

import { getJson, pathId, sendJson } from "./documents-request";

export const ROUNDS = ["recruiter", "hiring_manager", "peers"] as const;
export type Round = (typeof ROUNDS)[number];

const text = z.string().default("");

export const prepContentSchema = z.object({
  schema: z.literal(1).default(1),
  target: text,
  expected: z
    .array(
      z.object({
        question: z.string(),
        basis: z.enum(["posting", "hiring_panel"]).default("posting"),
        round: z.enum(ROUNDS).default("hiring_manager"),
        why: text,
      }),
    )
    .default([]),
  gaps: z.array(z.object({ gap: z.string(), answer: text })).default([]),
  stories: z
    .array(
      z.object({
        title: text,
        situation: text,
        task: text,
        action: text,
        result: text,
      }),
    )
    .default([]),
  ask: z.array(z.string()).default([]),
  debrief: z
    .array(z.object({ on: text, asked: z.string(), notes: text }))
    .default([]),
});
export type PrepContent = z.infer<typeof prepContentSchema>;
export type Story = PrepContent["stories"][number];
export type Debrief = PrepContent["debrief"][number];

const noteSchema = z.object({ field: z.string(), level: z.string() }).loose();

const generationSchema = z.object({
  status: z.enum([
    "waiting",
    "queued",
    "running",
    "done",
    "failed",
    "cancelled",
  ]),
  error: z.string().nullable(),
  created_at: z.string(),
});

const messageSchema = z.object({
  id: z.number(),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  at: z.string(),
});
export type PracticeMessage = z.infer<typeof messageSchema>;

export const interviewPrepSchema = z.object({
  document: z
    .object({
      id: z.string(),
      draft: prepContentSchema,
      draft_revision: z.number(),
      notes: z.array(noteSchema),
      language: z.string(),
      versions: z.number(),
    })
    .nullable(),
  writing: generationSchema.nullable(),
  practice: z.array(messageSchema),
  practice_open: z.boolean(),
  stories_proposal: z.string().nullable(),
});
export type InterviewPrep = z.input<typeof interviewPrepSchema>;
export type InterviewPrepRead = z.infer<typeof interviewPrepSchema>;

const startedSchema = z.object({
  document_id: z.string(),
  writing: generationSchema,
});
const practicedSchema = z.object({ message: messageSchema });
const keptSchema = z.object({ proposal_id: z.string().nullable() });
const savedSchema = z.object({ draft_revision: z.number() }).loose();

export const interviewPrepKey = (jobId: string) =>
  ["documents", "interview-prep", jobId] as const;

const prepPath = (jobId: string) => `/jobs/${pathId(jobId)}/interview-prep`;
const documentPath = (documentId: string) => `/documents/${pathId(documentId)}`;

export const getInterviewPrep = (
  jobId: string,
  signal?: AbortSignal,
): Promise<InterviewPrepRead> =>
  getJson(prepPath(jobId), interviewPrepSchema, signal);

export const prepareInterview = (jobId: string) =>
  sendJson(prepPath(jobId), "POST", startedSchema);

export const sendPractice = (documentId: string, message: string) =>
  sendJson(`${documentPath(documentId)}/practice`, "POST", practicedSchema, {
    text: message,
  });

export const keepStories = (documentId: string, indexes: number[]) =>
  sendJson(`${documentPath(documentId)}/stories`, "POST", keptSchema, {
    indexes,
  });

/** A hand save of the whole preparation (the Debrief appends to it), against its revision. */
export const savePrep = (
  documentId: string,
  content: PrepContent,
  revision: number,
) =>
  sendJson(`${documentPath(documentId)}/draft`, "PUT", savedSchema, content, {
    "If-Match": String(revision),
  });

/** The start of the api's sentence when Prepare or Practice waits for the consent. */
export const CONSENT_REFUSAL = "Confirm what is sent";
