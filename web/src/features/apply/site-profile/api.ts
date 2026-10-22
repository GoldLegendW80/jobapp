import { z } from "zod";
import { API_BASE, request } from "@/lib/api";

const fieldSchema = z.object({
  field_key: z.string(),
  label: z.string(),
  kind: z.string(),
  value: z.unknown(),
  current_value: z.unknown(),
  source: z.string(),
  source_path: z.string(),
  required: z.boolean(),
  refusal: z.string().nullable(),
  options: z.array(z.string()),
  replace: z.boolean(),
  approved: z.boolean(),
});
const shotSchema = z.object({
  id: z.string(),
  kind: z.string(),
  section: z.string(),
  created_at: z.string(),
});
export const sectionSchema = z.object({
  section: z.string(),
  label: z.string(),
  position: z.number(),
  state: z.string(),
  error_code: z.string().nullable(),
  values_hash: z.string(),
  fields: z.array(fieldSchema),
  screenshots: z.array(shotSchema),
  events: z.array(z.object({ kind: z.string(), at: z.string() }).loose()),
});
export const runSchema = z.object({
  id: z.string(),
  url: z.string(),
  host: z.string(),
  site_kind: z.string(),
  state: z.string(),
  autosave: z.boolean(),
  autosave_confirmed: z.boolean(),
  needs: z.string().nullable(),
  error_code: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  cost: z.record(z.string(), z.unknown()),
  sections: z.array(sectionSchema),
});
export type ProfileRun = z.infer<typeof runSchema>;
export type ProfileSection = z.infer<typeof sectionSchema>;
export type ProfileField = z.infer<typeof fieldSchema>;
export const runKey = (id: string) => ["site-profile", id] as const;
const id = encodeURIComponent;
export const getRun = (runId: string, signal?: AbortSignal) =>
  request(`/site-profiles/${id(runId)}`, {
    signal,
    validate: (body) => runSchema.parse(body),
  });
export const startRun = (url: string, autosave_confirmed = false) =>
  request("/site-profiles", {
    method: "POST",
    body: { url, autosave_confirmed },
    validate: (body) => runSchema.parse(body),
  });
export const action = (runId: string, path: string, body?: unknown) =>
  request(`/site-profiles/${id(runId)}/${path}`, {
    method: "POST",
    body,
    validate: (answer) => runSchema.parse(answer),
  });
export const editField = (
  runId: string,
  section: string,
  field: string,
  changes: { value?: string; replace?: boolean },
) =>
  request(
    `/site-profiles/${id(runId)}/sections/${id(section)}/fields/${id(field)}`,
    { method: "PUT", body: changes, validate: (body) => runSchema.parse(body) },
  );
export const screenshot = (runId: string, section: string, shotId: string) =>
  `${API_BASE}/site-profiles/${id(runId)}/sections/${id(section)}/screenshots/${id(shotId)}`;
export const autosaveSentence =
  "This site saves as you type, so a section cannot be reviewed before it is written. Only empty fields will be filled.";
