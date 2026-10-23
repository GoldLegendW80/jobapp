import { z } from "zod";

import { request } from "@/lib/api";

/** api/app/opportunities/starter_router.py: the starter list of boards and sites (O5b). */
const entrySchema = z.object({
  key: z.string(),
  kind: z.enum(["board", "source"]),
  company: z.string(),
  ats: z.string().nullable(),
  org: z.string().nullable(),
  source: z.string().nullable(),
  query: z.string().nullable(),
  location: z.string().nullable(),
  why: z.string(),
  added: z.boolean(),
});
export type StarterEntry = z.infer<typeof entrySchema>;

const listSchema = z.object({
  targets: z.boolean(),
  entries: z.array(entrySchema),
  unmatched: z.array(z.string()),
  blocked: z.number(),
  version: z.number(),
  checked_on: z.string(),
});
export type StarterList = z.infer<typeof listSchema>;

const choiceSchema = z.object({
  list: listSchema,
  added: z.array(z.string()),
});
export type StarterChoice = z.infer<typeof choiceSchema>;

export const starterKey = ["opportunities", "starter"] as const;

export const getStarter = (signal?: AbortSignal): Promise<StarterList> =>
  request("/opportunities/starter", {
    signal,
    validate: (body) => listSchema.parse(body),
  });

/** Add the entries chosen to Search options, or take entries off the list for good. */
export const chooseStarter = (choice: {
  add?: string[];
  remove?: string[];
}): Promise<StarterChoice> =>
  request("/opportunities/starter", {
    method: "POST",
    body: { add: choice.add ?? [], remove: choice.remove ?? [] },
    validate: (body) => choiceSchema.parse(body),
  });

const ATS_NAMES: Record<string, string> = {
  ashby: "Ashby",
  greenhouse: "Greenhouse",
  lever: "Lever",
};
const SOURCE_NAMES: Record<string, string> = {
  google_careers: "Google Careers",
  vie: "Business France VIE",
};

/** Where an entry is read, in plain words: "via Ashby", "Google Careers, searched for ...". */
export function entrySource(entry: StarterEntry): string {
  if (entry.kind === "board")
    return `via ${ATS_NAMES[entry.ats ?? ""] ?? "its ATS"}`;
  const site = SOURCE_NAMES[entry.source ?? ""] ?? "A site";
  const where = entry.location ? ` in ${entry.location}` : "";
  return `${site}, searched for "${entry.query ?? ""}"${where}`;
}
