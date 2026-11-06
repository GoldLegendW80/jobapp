import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { queueKey } from "./queue-api";
import {
  applySettingsKey,
  getApplySettings,
  saveApplySettings,
  type ApplySettings,
} from "./settings-api";

/** The person's apply settings. */
export function useApplySettings() {
  return useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
  });
}

/** Saves a change of the apply settings; the settings it answers become the cached ones. */
export function useSaveApplySettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveApplySettings,
    onSuccess: (saved) => {
      client.setQueryData<ApplySettings>(applySettingsKey, saved);
    },
  });
}

/** Saves a change of Full auto's settings, then reads the queue again. */
export function useSaveFullAutoSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveApplySettings,
    onSuccess: (saved) => {
      client.setQueryData<ApplySettings>(applySettingsKey, saved);
      // Turning Full auto off sends held items to the queue as review items.
      void client.invalidateQueries({ queryKey: queueKey });
    },
  });
}
