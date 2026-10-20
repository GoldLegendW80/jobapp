import { z } from "zod";

import { isApiError, request } from "@/lib/api";

/** api/app/opportunities/onboarding_router.py, the wizard's state for the signed in person. */
const stateSchema = z.object({
  completed_at: z.string().nullable(),
  dismissed_at: z.string().nullable(),
  profile_empty: z.boolean(),
  done: z.array(z.string()),
  steps_left: z.array(z.string()),
  open_wizard: z.boolean(),
  show_card: z.boolean(),
});
export type OnboardingState = z.infer<typeof stateSchema>;

const completeSchema = z.object({
  state: stateSchema,
  sweep: z.enum(["started", "no_boards", "search_invalid", "queue_down"]),
  sweep_id: z.string().nullable(),
});
export type CompleteAnswer = z.infer<typeof completeSchema>;

const runningSchema = z.object({
  code: z.literal("sweep_running"),
  sweep_id: z.string(),
  state: stateSchema,
});

export const onboardingKey = ["opportunities", "onboarding"] as const;

export const getOnboarding = (signal?: AbortSignal): Promise<OnboardingState> =>
  request("/opportunities/onboarding", {
    signal,
    validate: (body) => stateSchema.parse(body),
  });

export const dismissOnboarding = (): Promise<OnboardingState> =>
  request("/opportunities/onboarding/dismiss", {
    method: "POST",
    validate: (body) => stateSchema.parse(body),
  });

/** "Start". A 409 (a sweep already runs) still finished the wizard: it reads as `running`. */
export async function completeOnboarding(): Promise<
  CompleteAnswer | { sweep: "running"; sweep_id: string }
> {
  try {
    return await request("/opportunities/onboarding/complete", {
      method: "POST",
      validate: (body) => completeSchema.parse(body),
    });
  } catch (error) {
    if (isApiError(error) && error.kind === "http" && error.status === 409) {
      const running = runningSchema.safeParse(error.body);
      if (running.success)
        return { sweep: "running", sweep_id: running.data.sweep_id };
    }
    throw error;
  }
}

/** Apply's own route (A11): the mode after a Like, only ever sent when the person changed it. */
export const saveLikeMode = (mode: "review" | "full_auto"): Promise<unknown> =>
  request("/apply/settings", {
    method: "PUT",
    body: { mode_after_like: mode },
  });
