/** The CV and letter templates: the catalog, their thumbnails, and deleting a person's own. */
import { z } from "zod";

import { API_BASE } from "@/lib/api";

import type { Kind } from "./documents-api";
import { getJson, pathId, removeAt } from "./documents-request";

export const templateSchema = z.object({
  slug: z.string(),
  version: z.number(),
  /** The person's own, captured from an uploaded CV. */
  user: z.boolean(),
  name: z.record(z.string(), z.string()),
  description: z.record(z.string(), z.string()),
  tags: z.array(z.string()),
  photo: z.string(),
  accent: z.string().nullable(),
  kinds: z.array(z.string()),
  /** Kind to address; empty for a person's own template. */
  thumbnails: z.record(z.string(), z.string()),
  /** Kind to the editor's counters in characters (the manifest's soft limits); null: none. */
  soft_limits: z
    .record(z.string(), z.record(z.string(), z.number().nullable()))
    .optional(),
});
export type Template = z.infer<typeof templateSchema>;

export const templatesSchema = z.object({ templates: z.array(templateSchema) });

export const templatesKey = ["documents", "templates"] as const;

export const listTemplates = (signal?: AbortSignal): Promise<Template[]> =>
  getJson("/document-templates", templatesSchema, signal).then(
    (answer) => answer.templates,
  );

/** A thumbnail; the api puts the template version in the address it gives (`Template.thumbnails`). */
export const templateThumbnailUrl = (slug: string, kind: Kind): string =>
  `${API_BASE}/document-templates/${pathId(slug)}/thumbnail?kind=${kind}`;

export const deleteTemplate = (slug: string): Promise<void> =>
  removeAt(`/document-templates/${pathId(slug)}`);
