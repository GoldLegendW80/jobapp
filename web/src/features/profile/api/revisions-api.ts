/** Calls of /api/profile/revisions (SPEC-profile.md): the saves of a section, for the History drawer. */
import { z } from "zod";

import { request } from "@/lib/api";

import {
  EMPTY,
  SECTIONS,
  type SectionData,
  type SectionName,
} from "./profile-api";

export const revisionSummarySchema = z.object({
  revision: z.number().int().positive(),
  section: z.enum(SECTIONS),
  created_at: z.string(),
});
export type RevisionSummary = z.infer<typeof revisionSummarySchema>;

export const revisionPageSchema = z.object({
  items: z.array(revisionSummarySchema),
  next_before: z.number().int().positive().nullable(),
});
export type RevisionPage = z.infer<typeof revisionPageSchema>;

export const revisionSchema = revisionSummarySchema.extend({
  data: z.record(z.string(), z.unknown()),
});
export interface Revision extends RevisionSummary {
  /** The section as saved then, merged over its empty form like a loaded profile. */
  data: SectionData[SectionName];
}

/** Revisions per page of the History drawer. */
export const REVISIONS_PAGE = 20;

export const revisionsKey = (section: SectionName) =>
  ["profile", "revisions", section] as const;
export const revisionKey = (revision: number) =>
  ["profile", "revision", revision] as const;

/** One page of a section's saves, newest first; `before` (exclusive) pages back. */
export function listRevisions(
  section: SectionName,
  before?: number,
  signal?: AbortSignal,
): Promise<RevisionPage> {
  const query = new URLSearchParams({
    section,
    limit: String(REVISIONS_PAGE),
  });
  if (before !== undefined) query.set("before", String(before));
  return request(`/profile/revisions?${query.toString()}`, {
    signal,
    validate: (body) => revisionPageSchema.parse(body),
  });
}

export function getRevision(
  revision: number,
  signal?: AbortSignal,
): Promise<Revision> {
  return request(`/profile/revisions/${String(revision)}`, {
    signal,
    validate: (body) => {
      const parsed = revisionSchema.parse(body);
      return {
        ...parsed,
        data: {
          ...EMPTY[parsed.section],
          ...parsed.data,
        },
      };
    },
  });
}
