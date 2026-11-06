/**
 * The job titles agent's suggestions (/api/profile/title-suggestions,
 * api/app/profile/titles_router.py). GET never calls a model: it answers the stored suggestions,
 * already checked by the server against the saved search; POST is "More ideas" or a message to
 * the agent, one run; POST /dismiss is "Not for me"; DELETE /messages forgets what the person
 * wrote.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { request } from "@/lib/api";

import type { Identity } from "./api";
import { termProblem } from "./matching";

const decimal = z.union([z.string(), z.number()]);

export const suggestionSchema = z.object({
  title: z.string(),
  reason: z.string(),
  language: z.string(),
});
export type TitleSuggestion = z.infer<typeof suggestionSchema>;

export const titleSuggestionsSchema = z.object({
  status: z.enum(["idle", "running", "failed"]),
  /** What a run needs first: a model key, or something to read (a CV, a profile, a message). */
  needs: z.enum(["model_key", "input"]).nullable(),
  /** Oldest first: a run's new ideas join after the ones shown. */
  suggestions: z.array(suggestionSchema),
  refreshed_at: z.string().nullable(),
  error: z.string().nullable(),
  /** The last ask ended well and added nothing. */
  note: z.string().nullable(),
  /** What the person told the assistant, oldest first (the last few are kept). */
  messages: z.array(z.object({ text: z.string(), at: z.string() })),
  message_max: z.number(),
  /** Null without a model key; `cost` on an API key only. */
  estimate: z
    .object({
      tokens: z.object({ low: z.number(), high: z.number() }),
      cost: z
        .object({ currency: z.string(), low: decimal, high: decimal })
        .nullable(),
    })
    .nullable(),
  cost_note: z.string().nullable(),
});
export type TitleSuggestions = z.infer<typeof titleSuggestionsSchema>;

export const titleSuggestionsKey = ["title-suggestions"] as const;
/** How often a page asks again while a run is on its way. */
export const RUNNING_POLL_MS = 3000;

export function getTitleSuggestions(
  signal?: AbortSignal,
): Promise<TitleSuggestions> {
  return request("/profile/title-suggestions", {
    signal,
    validate: (data) => titleSuggestionsSchema.parse(data),
  });
}

/** "More ideas", or, with `message`, what the person tells the assistant: one run each. */
export function askMoreTitles(message?: string): Promise<TitleSuggestions> {
  return request("/profile/title-suggestions", {
    method: "POST",
    body: message === undefined ? {} : { message },
    validate: (data) => titleSuggestionsSchema.parse(data),
  });
}

/** "Not for me": the idea leaves and never comes back. */
export function dismissTitle(title: string): Promise<TitleSuggestions> {
  return request("/profile/title-suggestions/dismiss", {
    method: "POST",
    body: { title },
    validate: (data) => titleSuggestionsSchema.parse(data),
  });
}

/** The assistant forgets what the person wrote to it. */
export function clearTitleMessages(): Promise<TitleSuggestions> {
  return request("/profile/title-suggestions/messages", {
    method: "DELETE",
    validate: (data) => titleSuggestionsSchema.parse(data),
  });
}

export function useTitleSuggestions(enabled = true) {
  return useQuery({
    queryKey: titleSuggestionsKey,
    queryFn: ({ signal }) => getTitleSuggestions(signal),
    enabled,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? RUNNING_POLL_MS : false,
  });
}

/** One title, case, accents, spacing and a plural ending aside (the server's rule). */
export function sameTitle(a: string, b: string): boolean {
  const fold = (text: string) =>
    text
      .normalize("NFKD")
      .replace(/\p{M}/gu, "")
      .toLocaleLowerCase("en")
      .replace(/[\s\-_/.]+/g, " ")
      .trim();
  const x = fold(a);
  const y = fold(b);
  return ["", "s", "es"].some((end) => x === y + end || y === x + end);
}

/** The place of the profile's identity: the city, else the country; null when neither is a
 * plain word or phrase the Places list takes. */
export function placeOf(identity: Identity | undefined): string | null {
  for (const raw of [identity?.city, identity?.country]) {
    const place = (raw ?? "").normalize("NFC").trim().replace(/\s+/g, " ");
    if (place && termProblem(place, false) === undefined) return place;
  }
  return null;
}
