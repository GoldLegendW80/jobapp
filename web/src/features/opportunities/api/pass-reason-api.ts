/**
 * The optional reason of a Pass (api PUT /api/postings/{id}/pass/reason): stored on the Pass
 * event, counted by Your search in numbers and read by nothing else.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { request } from "@/lib/api";

import type { PassReason } from "@/features/opportunities/helpers/deck/pass-reasons";
import { insightsKey } from "@/features/jobs/api/insights-api";

const reasonSchema = z.object({ id: z.string(), reason: z.string() });

export const setPassReason = (postingId: string, reason: PassReason) =>
  request(`/postings/${encodeURIComponent(postingId)}/pass/reason`, {
    method: "PUT",
    body: { reason },
    timeoutMs: 15_000,
    validate: (body) => reasonSchema.parse(body),
  });

export function useSetPassReason(postingId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (reason: PassReason) => setPassReason(postingId, reason),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: insightsKey });
    },
  });
}
