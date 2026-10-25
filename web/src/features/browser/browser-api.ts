/**
 * Calls of /api/browser (SPEC-browser.md, API): the person's browser, their session in the live
 * view, the live view's one use ticket, and who holds the keyboard and the mouse. The user always
 * comes from the session; no lease id is ever sent.
 */
import { z } from "zod";

import { request } from "@/lib/api";

const leaseSchema = z.object({
  id: z.string(),
  purpose: z.string(),
  // false: the person's own session, where the person has the input from start to end.
  automation: z.boolean(),
  state: z.enum(["starting", "active", "waiting_person"]),
  wait_reason: z.string().nullable(),
  // The host of the address browserd noted for a sign in (B12b), never the assistant's words.
  wait_host: z.string().nullable(),
  wait_deadline: z.string().nullable(),
  // The whole minutes a sign in hand off still waits for the person, rounded up (browser B12a).
  wait_minutes_left: z.number().nullable().optional(),
  started_at: z.string(),
  active_s: z.number(),
  max_s: z.number(),
  // The id of the application a submit lease sends (apply), else null.
  application_id: z.string().nullable().optional(),
});
export type Lease = z.infer<typeof leaseSchema>;

const queueSchema = z.object({
  purpose: z.string(),
  position: z.number(),
  estimated_wait_s: z.number(),
});
export type QueuePlace = z.infer<typeof queueSchema>;

export const browserSchema = z.object({
  available: z.boolean(),
  profile: z.object({
    exists: z.boolean(),
    size_bytes: z.number(),
    last_used_at: z.string().nullable(),
  }),
  lease: leaseSchema.nullable(),
  holder: z.enum(["you", "other"]).nullable(),
  queue: z.array(queueSchema),
});
export type Browser = z.infer<typeof browserSchema>;

const sessionSchema = z.object({
  lease: leaseSchema.nullable(),
  queue: queueSchema.nullable(),
});
export type Session = z.infer<typeof sessionSchema>;

const ticketSchema = z.object({
  ticket: z.string(),
  url: z.string().startsWith("/vnc/websockify?ticket="),
  expires_in_s: z.number(),
});
export type Ticket = z.infer<typeof ticketSchema>;

const inputSchema = z.object({ input: z.enum(["you", "assistant"]) });
export type InputHolder = z.infer<typeof inputSchema>["input"];

export const browserQueryKey = ["browser"] as const;
// Starting Chromium on a cold profile takes a few seconds (browserd waits up to 30 s).
const START_TIMEOUT_MS = 40_000;

export function getBrowser(signal?: AbortSignal): Promise<Browser> {
  return request("/browser", {
    signal,
    validate: (data) => browserSchema.parse(data),
  });
}

/**
 * A session of the person, or their place in the queue: "Look at my browser", or "Sign in to a
 * site" at the `address` the person typed (the api answers 422 `bad_address` when it is not an
 * http or https address with a host, and starts nothing).
 */
export function startSession(
  purpose: "look" | "signin",
  address?: string,
): Promise<Session> {
  return request("/browser/sessions", {
    method: "POST",
    body: address === undefined ? { purpose } : { purpose, address },
    timeoutMs: START_TIMEOUT_MS,
    validate: (data) => sessionSchema.parse(data),
  });
}

export function endSession(): Promise<void> {
  return request("/browser/sessions/current", { method: "DELETE" });
}

/** "Forget all logins": ends any session of the person and deletes their browser's profile. */
export function forgetLogins(): Promise<void> {
  return request("/browser/profile", { method: "DELETE" });
}

/** A ticket for one websocket of the live view, bound to this tab's `viewer` id. */
export function liveTicket(viewer: string): Promise<Ticket> {
  return request("/browser/live/ticket", {
    method: "POST",
    body: { viewer },
    validate: (data) => ticketSchema.parse(data),
  });
}

export function takeOver(viewer: string): Promise<InputHolder> {
  return request("/browser/takeover", {
    method: "POST",
    body: { viewer },
    validate: (data) => inputSchema.parse(data).input,
  });
}

/** Gives the input back to the assistant, only if this view (`viewer`) is the one that holds it. */
export function giveBack(viewer: string): Promise<InputHolder> {
  return request("/browser/giveback", {
    method: "POST",
    body: { viewer },
    validate: (data) => inputSchema.parse(data).input,
  });
}

/** "I'm signed in" of a sign in hand off: the assistant goes on at the same page. */
export function handoffDone(): Promise<void> {
  return request("/browser/handoff/done", {
    method: "POST",
    timeoutMs: START_TIMEOUT_MS,
  });
}

/** "Cancel" of a sign in hand off: the assistant stops and the work waits for the person. */
export function handoffCancel(): Promise<void> {
  return request("/browser/handoff/cancel", { method: "POST" });
}

/** The websocket address of a ticket, on the page's own host (Caddy routes /vnc/websockify). */
export function liveSocketUrl(ticket: Ticket, location: Location): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${ticket.url}`;
}

/** The queue's sentence, in whole minutes rounded up. */
export function waitText(place: QueuePlace): string {
  const minutes = Math.max(1, Math.ceil(place.estimated_wait_s / 60));
  return minutes === 1
    ? "Your browser starts in about a minute."
    : `Your browser starts in about ${minutes} minutes.`;
}

const MB = 1_000_000;

/** The profile's size as Forget all logins says it, in whole megabytes (decimal units). */
export function sizeText(bytes: number): string {
  if (bytes < MB) return "less than 1 MB";
  const megabytes = Math.round(bytes / MB);
  if (megabytes < 1000) return `about ${String(megabytes)} MB`;
  return `about ${(bytes / (1000 * MB)).toFixed(1)} GB`;
}
