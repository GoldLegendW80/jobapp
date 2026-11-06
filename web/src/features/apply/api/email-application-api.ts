/** The e-mail of an application sent by e-mail: the complete message, shown before approval. */
import { z } from "zod";

import { request } from "@/lib/api";

const emailSchema = z.object({
  id: z.string(),
  status: z.string(),
  to: z.array(z.string()),
  subject: z.string(),
  body: z.string(),
  send_id: z.string().nullable(),
  message: z.string().nullable(),
  issues: z.array(
    z.object({ code: z.string(), start: z.number(), end: z.number() }),
  ),
});
export type EmailApplication = z.infer<typeof emailSchema>;

export const emailApplicationKey = (applicationId: string) =>
  ["email-application", applicationId] as const;

export const getEmailApplication = (
  applicationId: string,
): Promise<EmailApplication> =>
  request(`/applications/${applicationId}/email`, {
    validate: (answer) => emailSchema.parse(answer),
  });
