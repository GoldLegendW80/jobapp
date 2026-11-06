/** Calls of /api/mail/sends: the sends of the person's mail. */
import { z } from "zod";

import { request } from "@/lib/api";

const reconciledSchema = z.object({ resolved: z.number() });
export type Reconciled = z.infer<typeof reconciledSchema>;

/** "Check Sent": looks in the person's Sent folder for a send whose outcome is unknown. */
export const reconcileSend = (
  sendId: string | null | undefined,
): Promise<Reconciled> =>
  request(`/mail/sends/${sendId}/reconcile`, {
    method: "POST",
    validate: (answer) => reconciledSchema.parse(answer),
  });
