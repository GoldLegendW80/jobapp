/**
 * Calls of /api/browser/handoff/later: a sign in the work waits for, opened later by the person
 * from the place that registered it (`module` and `ref`), then I'm signed in or Cancel.
 */
import { z } from "zod";

import { request } from "@/lib/api";

import { browserSchema } from "./browser-api";

const sessionSchema = z.object({
  lease: browserSchema.shape.lease,
  queue: z.object({ position: z.number() }).nullable(),
});
export type LaterSession = z.infer<typeof sessionSchema>;

/** The waiting work a later sign in belongs to. */
export type LaterHandoff = { module: string; ref: string };

export function openLaterHandoff(body: LaterHandoff): Promise<LaterSession> {
  return request("/browser/handoff/later", {
    method: "POST",
    body,
    timeoutMs: 40_000,
    validate: (data) => sessionSchema.parse(data),
  });
}

export function answerLaterHandoff(
  action: "done" | "cancel",
  body: LaterHandoff,
): Promise<unknown> {
  return request(`/browser/handoff/later/${action}`, { method: "POST", body });
}
