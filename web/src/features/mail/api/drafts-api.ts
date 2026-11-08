/**
 * Calls of /api/mail/drafts (SPEC-mail.md, "Drafting and sending"): one draft with what the
 * gate's fact check finds in it, and Send, the person's own click on that exact draft. The send
 * goes through `mail.gate.send` alone, with every rule of the gate.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { request } from "@/lib/api";

const draftSchema = z.object({
  id: z.string(),
  status: z.string(),
  to: z.array(z.string()),
  cc: z.array(z.string()),
  subject: z.string(),
  body: z.string(),
  content_hash: z.string(),
  issues: z.array(
    z.object({
      id: z.string(),
      code: z.string(),
      start: z.number(),
      end: z.number(),
    }),
  ),
  send_result: z.string().nullable(),
  message: z.string().nullable(),
});
export type MailDraft = z.infer<typeof draftSchema>;

const sentSchema = z.object({
  id: z.string(),
  result: z.string(),
  code: z.string().nullable(),
  detail: z.string().nullable(),
});
export type MailSendResult = z.infer<typeof sentSchema>;

export const mailDraftKey = (id: string) => ["mail-draft", id] as const;

export const getMailDraft = (id: string, signal?: AbortSignal) =>
  request(`/mail/drafts/${encodeURIComponent(id)}`, {
    signal,
    validate: (answer) => draftSchema.parse(answer),
  });

/** Send: the confirmed recipients and the issues the person acknowledged, on that exact text. */
export const sendMailDraft = (
  draft: MailDraft,
  acknowledged: readonly string[],
): Promise<MailSendResult> =>
  request(`/mail/drafts/${encodeURIComponent(draft.id)}/send`, {
    method: "POST",
    timeoutMs: 60_000,
    body: {
      content_hash: draft.content_hash,
      confirmed_new_recipients: [...draft.to, ...draft.cc],
      acknowledged_issues: acknowledged,
    },
    validate: (answer) => sentSchema.parse(answer),
  });

export function useMailDraft(id: string | null) {
  return useQuery({
    queryKey: mailDraftKey(id ?? ""),
    queryFn: ({ signal }) => getMailDraft(id ?? "", signal),
    enabled: id !== null,
  });
}

export function useSendMailDraft(onSent: (result: MailSendResult) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      draft,
      acknowledged,
    }: {
      draft: MailDraft;
      acknowledged: readonly string[];
    }) => sendMailDraft(draft, acknowledged),
    onSuccess: (result, { draft }) => {
      void client.invalidateQueries({ queryKey: mailDraftKey(draft.id) });
      onSent(result);
    },
  });
}
