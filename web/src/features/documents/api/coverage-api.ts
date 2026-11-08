/**
 * The keyword coverage of a posting's CV (SPEC-documents.md, "Keyword coverage"): the requirement
 * terms of the posting that the draft names, and those it does not. Empty for any other document.
 */
import { z } from "zod";

import { DOCUMENTS_ROOT, getJson, pathId } from "./documents-request";

export const coverageSchema = z.object({
  draft_revision: z.number(),
  terms: z.array(z.string()),
  missing: z.array(z.string()),
});
export type Coverage = z.infer<typeof coverageSchema>;

/** Keyed by the draft's revision: every save and every write reads it again, nothing else. */
export const coverageKey = (id: string, revision: number) =>
  ["documents", "document", id, "coverage", revision] as const;

export const getCoverage = (
  documentId: string,
  signal?: AbortSignal,
): Promise<Coverage> =>
  getJson(
    `${DOCUMENTS_ROOT}/${pathId(documentId)}/coverage`,
    coverageSchema,
    signal,
  );
