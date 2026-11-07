/** Calls of /api/profile/export and /api/profile/import (SPEC-profile.md): the profile as a file. */
import { z } from "zod";

import { request } from "@/lib/api";

import { profileSchema, toProfile, type Profile } from "./profile-api";

/** What the api reads (schemas.py EXPORT_FORMAT, EXPORT_VERSION, IMPORT_MAX_BYTES). */
export const EXPORT_FORMAT = "jobapp-profile";
export const EXPORT_VERSION = 1;
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024;

/** The export file as the api wrote it (GET /api/profile/export), decoded. */
export function exportProfile(): Promise<Record<string, unknown>> {
  return request("/profile/export", {
    // With a 2 MB photo inside, a phone link needs longer than the default.
    timeoutMs: 30_000,
    validate: (body) => z.record(z.string(), z.unknown()).parse(body),
  });
}

/** Replace the whole profile with an export file, based on the revision the page loaded. */
export function importProfile(
  document: Record<string, unknown>,
  baseRevision: number,
): Promise<Profile> {
  return request("/profile/import", {
    method: "POST",
    body: { ...document, base_revision: baseRevision },
    timeoutMs: 30_000,
    validate: (body) => toProfile(profileSchema.parse(body)),
  });
}
