/**
 * Apply myself's calls (apply A9, api/app/apply/manual_router.py): the session's status, asking
 * for it, the panel's three actions, recording the application as sent and closing it.
 */
import { z } from "zod";

import { request } from "@/lib/api";

import { applicationSchema, type Application } from "./apply-api";

const TIMEOUT_MS = 15_000;

const manualSchema = z.object({
  state: z.string(),
  phase: z.enum(["waiting", "open", "ended"]).nullable(),
  position: z.number().int().nullable().optional(),
  documents: z.array(z.string()),
  confirmation_seen: z.boolean(),
  chooser: z.boolean(),
  idle_left_s: z.number().int().nullable().optional(),
  idle_warning: z.boolean(),
  busy: z.string().nullable().optional(),
  last: z.object({ action: z.string(), outcome: z.string() }).nullable(),
  warnings: z.array(z.object({ code: z.string(), sentence: z.string() })),
});
export type ManualStatus = z.infer<typeof manualSchema>;

export type ManualAction = "attach_cv" | "attach_letter" | "fill_details";

export const manualKey = (applicationId: string) =>
  ["apply", "manual", applicationId] as const;

const base = (applicationId: string) =>
  `/applications/${encodeURIComponent(applicationId)}/manual`;

export const getManual = (
  applicationId: string,
  signal?: AbortSignal,
): Promise<ManualStatus> =>
  request(base(applicationId), {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => manualSchema.parse(body),
  });

/** Asks for the session; asked again while it waits for the browser, to keep its place. */
export const openManual = (applicationId: string): Promise<ManualStatus> =>
  request(`${base(applicationId)}/open`, {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => manualSchema.parse(body),
  });

export const actManual = (
  applicationId: string,
  action: ManualAction,
): Promise<ManualStatus> =>
  request(`${base(applicationId)}/actions`, {
    method: "POST",
    body: { action },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => manualSchema.parse(body),
  });

/** `seen`: the page showed a confirmation ("Record as applied"); else "I submitted it". */
export const recordManual = (
  applicationId: string,
  seen: boolean,
): Promise<Application> =>
  request(`${base(applicationId)}/record`, {
    method: "POST",
    body: { seen },
    timeoutMs: TIMEOUT_MS,
    validate: (body) => applicationSchema.parse(body),
  });

export const closeManual = (applicationId: string): Promise<Application> =>
  request(`${base(applicationId)}/close`, {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => applicationSchema.parse(body),
  });
