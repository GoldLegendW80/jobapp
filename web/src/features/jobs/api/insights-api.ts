/**
 * Your search in numbers (api/app/opportunities/insights_router.py): the person's own counts,
 * and the close of the silent applications they ticked, undone by the bulk change's undo.
 * A rate or a median the api leaves null is under its floor: too few to say.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { request } from "@/lib/api";

import { jobsKey, undoBulk } from "./jobs-api";

const groupSchema = z.object({
  key: z.string(),
  applied: z.number().int(),
  answered: z.number().int(),
  interviews: z.number().int(),
  rate: z.number().int().nullable(),
});
export type Group = z.infer<typeof groupSchema>;

const silentSchema = z.object({
  id: z.string(),
  company: z.string(),
  title: z.string(),
  applied_on: z.string().nullable(),
  last_signal: z.string(),
  days: z.number().int(),
});
export type SilentApplication = z.infer<typeof silentSchema>;

const insightsSchema = z.object({
  silent_days: z.number().int(),
  funnel: z.object({
    found: z.number().int(),
    liked: z.number().int(),
    applied: z.number().int(),
    answered: z.number().int(),
    interview: z.number().int(),
    rejected: z.number().int(),
    passed: z.number().int(),
    waiting: z.number().int(),
    answer_rate: z.number().int().nullable(),
    interview_rate: z.number().int().nullable(),
  }),
  by_fit: z.array(groupSchema),
  by_ats: z.array(groupSchema),
  by_source: z.array(groupSchema),
  first_answer: z.object({
    median_days: z.number().nullable(),
    measured: z.number().int(),
    undated: z.number().int(),
    waiting: z.number().int(),
  }),
  pass_reasons: z.array(
    z.object({ reason: z.string(), count: z.number().int() }),
  ),
  passes_without_reason: z.number().int(),
  silent: z.array(silentSchema),
  silent_undated: z.number().int(),
});
export type Insights = z.infer<typeof insightsSchema>;

const closedSchema = z.object({
  closed: z.array(z.string()),
  skipped: z.array(z.string()),
  undo_token: z.string().nullable(),
  undo_expires_at: z.string().nullable(),
});

export const insightsKey = ["insights"] as const;

export const getInsights = (signal?: AbortSignal): Promise<Insights> =>
  request("/insights", {
    signal,
    timeoutMs: 15_000,
    validate: (body) => insightsSchema.parse(body),
  });

export const closeSilent = (ids: string[]) =>
  request("/insights/silent/close", {
    method: "POST",
    body: { ids },
    timeoutMs: 30_000,
    validate: (body) => closedSchema.parse(body),
  });

export function useInsights() {
  return useQuery({
    queryKey: insightsKey,
    queryFn: ({ signal }) => getInsights(signal),
  });
}

/** The close of the ticked silent applications, and its undo; both read the numbers again. */
export function useCloseSilent() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: insightsKey });
    void client.invalidateQueries({ queryKey: jobsKey });
  };
  const close = useMutation({
    mutationFn: closeSilent,
    onSettled: refresh,
  });
  const undo = useMutation({ mutationFn: undoBulk, onSettled: refresh });
  return { close, undo };
}
