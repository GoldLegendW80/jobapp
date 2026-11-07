import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { baseCvsKey } from "./base-cvs-api";
import { documentKey, patchDocument, type DocumentOut } from "./documents-api";
import { postingDocumentsKey } from "./posting-documents-api";
import { listTemplates, templatesKey } from "./templates-api";

/** Every template the api serves. */
export function useTemplates(options: { staleTime?: number } = {}) {
  return useQuery({
    queryKey: templatesKey,
    queryFn: ({ signal }) => listTemplates(signal),
    ...options,
  });
}

/** Switches a document's template, then refreshes what shows it: the document, its versions, and the list it sits in. */
export function useTemplateSwitch(document: DocumentOut | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      document === undefined
        ? Promise.reject(new Error("No document to switch."))
        : patchDocument(document.id, { template_slug: slug }),
    onSuccess: (answer) => {
      client.setQueryData(documentKey(answer.id), answer);
    },
    onSettled: () => {
      if (document === undefined) return;
      void client.invalidateQueries({ queryKey: documentKey(document.id) });
      if (document.scope === "base")
        void client.invalidateQueries({ queryKey: baseCvsKey });
      if (document.posting_id !== null)
        void client.invalidateQueries({
          queryKey: postingDocumentsKey(document.posting_id),
        });
    },
  });
}
