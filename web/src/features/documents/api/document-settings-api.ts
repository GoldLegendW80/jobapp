/**
 * The person's writing rules for every document: no dash separators, the banned phrases, the
 * starter list of common phrases (off by default), which the api answers with but never saves,
 * and the words a letter aims at (`letter_words`, 350 when an older api leaves it out).
 */
import { z } from "zod";

import { DOCUMENTS_ROOT, getJson, sendJson } from "./documents-request";

/** settings.py: the default of `letter_words`, and LETTER_WORDS_MIN and LETTER_WORDS_MAX. */
export const LETTER_WORDS = 350;
export const LETTER_WORDS_MIN = 150;
export const LETTER_WORDS_MAX = 600;

export const settingsSchema = z.object({
  no_dash_separators: z.boolean(),
  banned_phrases: z.array(z.string()),
  starter_phrases: z.boolean().default(false),
  starter_list: z.array(z.string()).default([]),
  letter_words: z.number().int().default(LETTER_WORDS),
});
export type DocumentSettings = z.infer<typeof settingsSchema>;

export const settingsKey = ["documents", "settings"] as const;

export const getSettings = (signal?: AbortSignal): Promise<DocumentSettings> =>
  getJson(`${DOCUMENTS_ROOT}/settings`, settingsSchema, signal);

export const putSettings = (
  value: DocumentSettings,
): Promise<DocumentSettings> =>
  sendJson(`${DOCUMENTS_ROOT}/settings`, "PUT", settingsSchema, {
    no_dash_separators: value.no_dash_separators,
    banned_phrases: value.banned_phrases,
    starter_phrases: value.starter_phrases,
    letter_words: value.letter_words,
  });
