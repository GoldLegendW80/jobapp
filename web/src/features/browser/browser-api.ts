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
  state: z.enum(["starting", "active", "waiting_person"]),
  wait_reason: z.string().nullable(),
  wait_site: z.string().nullable(),
  wait_deadline: z.string().nullable(),
  started_at: z.string(),
  active_s: z.number(),
  max_s: z.number(),
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

/** "Look at my browser": a session of the person, or their place in the queue. */
export function startSession(purpose: "look" | "signin"): Promise<Session> {
  return request("/browser/sessions", {
    method: "POST",
    body: { purpose },
    timeoutMs: START_TIMEOUT_MS,
    validate: (data) => sessionSchema.parse(data),
  });
}

export function endSession(): Promise<void> {
  return request("/browser/sessions/current", { method: "DELETE" });
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
