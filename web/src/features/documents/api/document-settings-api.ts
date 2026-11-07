/** The person's writing rules for every document: no dash separators, the banned phrases. */
import { z } from "zod";

import { DOCUMENTS_ROOT, getJson, sendJson } from "./documents-request";

export const settingsSchema = z.object({
  no_dash_separators: z.boolean(),
  banned_phrases: z.array(z.string()),
});
export type DocumentSettings = z.infer<typeof settingsSchema>;

export const settingsKey = ["documents", "settings"] as const;

export const getSettings = (signal?: AbortSignal): Promise<DocumentSettings> =>
  getJson(`${DOCUMENTS_ROOT}/settings`, settingsSchema, signal);

export const putSettings = (
  value: DocumentSettings,
): Promise<DocumentSettings> =>
  sendJson(`${DOCUMENTS_ROOT}/settings`, "PUT", settingsSchema, value);
