/** Calls of /api/profile/photo (SPEC-profile.md): the photo a CV shows. */
import { z } from "zod";

import { request } from "@/lib/api";

export const photoSchema = z.object({
  mime: z.enum(["image/png", "image/jpeg"]),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  size: z.number().int().positive(),
  etag: z.string().min(1),
  updated_at: z.string(),
});
export type StoredPhoto = z.infer<typeof photoSchema>;

/** Upload the image bytes as the raw body (the api sniffs the type from them). */
export function uploadPhoto(image: Blob): Promise<StoredPhoto> {
  return request("/profile/photo", {
    method: "PUT",
    rawBody: image,
    // A 2 MB upload on a slow phone link needs longer than the default.
    timeoutMs: 30_000,
    validate: (body) => photoSchema.parse(body),
  });
}

export async function deletePhoto(): Promise<void> {
  await request("/profile/photo", { method: "DELETE" });
}

/** The stored photo's address; the ETag in the query busts the browser cache when it changes. */
export function photoUrl(etag: string): string {
  return `/api/profile/photo?v=${encodeURIComponent(etag.replaceAll('"', ""))}`;
}
