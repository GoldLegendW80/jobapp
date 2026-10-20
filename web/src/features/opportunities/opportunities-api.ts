/**
 * Calls of the swipe deck (SPEC-opportunities.md, "API"; api/app/opportunities/router.py): the
 * deck itself, the Pass and its undo. The Like is apply's route (`likePosting` of
 * features/apply/queue-api.ts) and the undo of a Like is apply's cancel (`cancelApplication` of
 * features/apply/apply-api.ts): both are called as they are, never wrapped in new routes.
 */
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

const TIMEOUT_MS = 15_000;

const reasonSchema = z.object({
  matches: z.array(z.string()),
  gap: z.string().nullable(),
  text: z.string().nullable(),
});

export const cardSchema = z.object({
  id: z.string(),
  title: z.string(),
  company: z.string(),
  location: z.string(),
  contract_type: z.string(),
  source: z.string().nullable(),
  url: z.string().nullable(),
  fit: z.number().int().nullable(),
  found_on: z.string(),
  reason: reasonSchema,
  pay: z.string().nullable().optional(),
  work_mode: z.string().nullable().optional(),
});
export type Card = z.infer<typeof cardSchema>;

const deckSchema = z.object({
  cards: z.array(cardSchema),
  next_cursor: z.string().nullable(),
  left: z.number().int(),
  mode: z.enum(["review", "full_auto"]),
  min_fit: z.number().int(),
  rules_unreadable: z.boolean(),
});
export type DeckPage = z.infer<typeof deckSchema>;
export type Mode = DeckPage["mode"];

const passSchema = z.object({
  id: z.string(),
  status: z.string(),
  changed: z.boolean(),
});

/** The 409 of a swipe: the sentence for the person, the code, and the application it names. */
const refusedSchema = z.object({
  detail: z.string(),
  code: z.string(),
  application_id: z.string().nullable().optional(),
});
export type Refused = z.infer<typeof refusedSchema>;

export type Remote = "remote" | "on_site";
export interface DeckFilters {
  types: readonly string[];
  remote: Remote | null;
  source: string | null;
}
export const NO_FILTERS: DeckFilters = {
  types: [],
  remote: null,
  source: null,
};

export const deckRootKey = ["opportunities", "deck"] as const;
export const deckKey = (filters: DeckFilters) =>
  [...deckRootKey, filters.types, filters.remote, filters.source] as const;

const id = encodeURIComponent;

export function deckQuery(filters: DeckFilters, cursor: string | null): string {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  for (const type of filters.types) params.append("type[]", type);
  if (filters.remote) params.set("remote", filters.remote);
  if (filters.source) params.set("source", filters.source);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const getDeck = (
  filters: DeckFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<DeckPage> =>
  request(`/postings/deck${deckQuery(filters, cursor)}`, {
    signal,
    timeoutMs: TIMEOUT_MS,
    validate: (body) => deckSchema.parse(body),
  });

export const passPosting = (postingId: string) =>
  request(`/postings/${id(postingId)}/pass`, {
    method: "POST",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => passSchema.parse(body),
  });

export const undoPass = (postingId: string) =>
  request(`/postings/${id(postingId)}/pass`, {
    method: "DELETE",
    timeoutMs: TIMEOUT_MS,
    validate: (body) => passSchema.parse(body),
  });

/** The refusal a swipe was answered with (409), or undefined for any other error. */
export function refusedOf(error: unknown): Refused | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const parsed = refusedSchema.safeParse(error.body);
  return parsed.success ? parsed.data : undefined;
}

export const isGone = (error: unknown): boolean =>
  isApiError(error) && error.status === 404;
