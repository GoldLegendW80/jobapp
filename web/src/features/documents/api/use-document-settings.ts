import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getSettings,
  putSettings,
  settingsKey,
  type DocumentSettings,
} from "./document-settings-api";

/** The writing rules of the person's documents. */
export function useDocumentSettings() {
  return useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => getSettings(signal),
  });
}

/**
 * Saves the whole value of the writing rules; the api's answer goes to the cache. A save carries
 * its `value` and whatever the caller needs back in `onError` and `onSettled`.
 */
export function useSaveDocumentSettings<
  Save extends { value: DocumentSettings },
>(on: {
  onSaved: () => void;
  onError: (save: Save) => void;
  onSettled: (save: Save) => void;
}) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ value }: Save) => putSettings(value),
    // Each PUT is the whole value: one at a time, so the last one asked is the last one kept.
    scope: { id: "documents-settings" },
    onSuccess: (value) => {
      client.setQueryData(settingsKey, value);
      on.onSaved();
    },
    onError: (_error, save) => {
      on.onError(save);
    },
    onSettled: (_data, _error, save) => {
      on.onSettled(save);
    },
  });
}
