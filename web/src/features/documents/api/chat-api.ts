/** The chat of a document: its messages on a channel (content or design), and sending one. */
import { z } from "zod";

import { generationSchema, type Channel } from "./documents-api";
import { DOCUMENTS_ROOT, getJson, pathId, sendJson } from "./documents-request";

export const messageSchema = z.object({
  id: z.number(),
  channel: z.enum(["content", "design"]),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  generation_id: z.string().nullable(),
  version_number: z.number().nullable(),
  at: z.string(),
  // A message of the person that corrects the wording: the rule the chat offers to keep.
  rule: z.string().nullish(),
});
export type Message = z.infer<typeof messageSchema>;

export const messagesSchema = z.object({ messages: z.array(messageSchema) });

/** What sending a message answers: the message and the generation it started (202). */
export const sentMessageSchema = z.object({
  message: messageSchema,
  generation: generationSchema,
});
export type SentMessage = z.infer<typeof sentMessageSchema>;

export const messagesKey = (id: string, channel: Channel = "content") =>
  ["documents", "document", id, "messages", channel] as const;

const messagesPath = (documentId: string) =>
  `${DOCUMENTS_ROOT}/${pathId(documentId)}/messages`;

export const listMessages = (
  documentId: string,
  channel: Channel = "content",
  signal?: AbortSignal,
): Promise<Message[]> =>
  getJson(
    `${messagesPath(documentId)}?channel=${channel}`,
    messagesSchema,
    signal,
  ).then((answer) => answer.messages);

export const sendMessage = (
  documentId: string,
  text: string,
  channel: Channel = "content",
): Promise<SentMessage> =>
  sendJson(messagesPath(documentId), "POST", sentMessageSchema, {
    text,
    channel,
  });
