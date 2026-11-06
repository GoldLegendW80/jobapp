import {
  useMutation,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { browserQueryKey } from "./browser-api";
import {
  answerLaterHandoff,
  openLaterHandoff,
  type LaterHandoff,
  type LaterSession,
} from "./later-handoff-api";

/** Opens the sign in the work waits for in the person's browser, then reads the browser again. */
export function useOpenLaterHandoff(
  body: LaterHandoff,
  handlers: Pick<UseMutationOptions<LaterSession>, "onSuccess">,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => openLaterHandoff(body),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries({ queryKey: browserQueryKey });
      return handlers.onSuccess?.(...args);
    },
  });
}

/** I'm signed in (`done`) or Cancel: the waiting work moves on, so every read is made again. */
export function useAnswerLaterHandoff(
  body: LaterHandoff,
  handlers: Pick<
    UseMutationOptions<unknown, Error, "done" | "cancel">,
    "onSuccess"
  >,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: "done" | "cancel") => answerLaterHandoff(action, body),
    ...handlers,
    onSuccess: (...args) => {
      void client.invalidateQueries();
      return handlers.onSuccess?.(...args);
    },
  });
}
