/** The person's base CVs: the list, a new one (from the profile, a copy or an upload), a rename or
 * a new default, a delete. */
import { z } from "zod";

import {
  BASE_SOURCES,
  generationSchema,
  languageSchema,
  renderStateSchema,
  type Language,
} from "./documents-api";
import {
  DOCUMENTS_ROOT,
  getJson,
  pathId,
  removeAt,
  sendJson,
} from "./documents-request";

export const baseCvSchema = z.object({
  id: z.string(),
  name: z.string(),
  source: z.enum(BASE_SOURCES),
  template_slug: z.string(),
  language: languageSchema,
  is_default: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
  last_version_at: z.string().nullable(),
  render_state: renderStateSchema,
  /** The open one, else the last one. */
  generation: generationSchema.nullable(),
  /** Design capture is independent of content conversion; old responses have none. */
  design_generation: generationSchema.nullable().default(null),
  /** How close the template copied from this CV lies on it (D28); older responses have none. */
  design_fidelity: z
    .object({ lines: z.number().int(), in_place: z.number().int() })
    .nullable()
    .default(null),
});
export type BaseCv = z.infer<typeof baseCvSchema>;

export const baseCvsSchema = z.object({
  base_cvs: z.array(baseCvSchema),
  max: z.number(),
});
export type BaseCvs = z.infer<typeof baseCvsSchema>;

const builtSchema = z.object({ base_cv: baseCvSchema });

export interface BaseCvRequest {
  from: "profile" | "copy" | "upload";
  name?: string;
  copy_of?: string;
  ingestion_document_id?: string;
  language?: Language;
}

export const baseCvsKey = ["documents", "base-cvs"] as const;

const BASE_CVS = `${DOCUMENTS_ROOT}/base-cvs`;

export const listBaseCvs = (signal?: AbortSignal): Promise<BaseCvs> =>
  getJson(BASE_CVS, baseCvsSchema, signal);

/** From the profile (202, the generation follows in the list) or a copy (201, made at once). */
export const createBaseCv = (body: BaseCvRequest): Promise<BaseCv> =>
  sendJson(BASE_CVS, "POST", builtSchema, body).then((built) => built.base_cv);

export const patchBaseCv = (
  baseId: string,
  change: { name?: string; default?: boolean },
): Promise<BaseCv> =>
  sendJson(`${BASE_CVS}/${pathId(baseId)}`, "PATCH", baseCvSchema, change);

export const deleteBaseCv = (baseId: string): Promise<void> =>
  removeAt(`${BASE_CVS}/${pathId(baseId)}`);
