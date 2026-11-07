import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  askMoreTitles,
  clearTitleMessages,
  dismissTitle,
  titleSuggestionsKey,
  type TitleSuggestions,
} from "./title-suggestions-api";

/** Each answer is the whole list of ideas: it replaces the cached one. */
function useTitleCache() {
  const client = useQueryClient();
  return {
    store: (data: TitleSuggestions) => {
      client.setQueryData(titleSuggestionsKey, data);
    },
    reread: () => {
      void client.invalidateQueries({ queryKey: titleSuggestionsKey });
    },
  };
}

/** "More ideas", or with a message what the person tells the assistant; a failure reads the ideas again. */
export function useAskMoreTitles(on: {
  onAsked: (message: string | undefined) => void;
}) {
  const { store, reread } = useTitleCache();
  return useMutation({
    mutationFn: (message: string | undefined) => askMoreTitles(message),
    onSuccess: (data, message) => {
      store(data);
      on.onAsked(message);
    },
    onError: reread,
  });
}

/** "Not for me"; a failure gives the title to `onError`, then reads the ideas again. */
export function useDismissTitle(on: { onError: (title: string) => void }) {
  const { store, reread } = useTitleCache();
  return useMutation({
    mutationFn: dismissTitle,
    onSuccess: store,
    onError: (_error, title) => {
      on.onError(title);
      reread();
    },
  });
}

/** The assistant forgets what the person wrote to it. */
export function useClearTitleMessages() {
  const { store } = useTitleCache();
  return useMutation({
    mutationFn: clearTitleMessages,
    onSuccess: store,
  });
}
