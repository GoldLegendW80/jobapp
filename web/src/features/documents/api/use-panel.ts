import { useQuery } from "@tanstack/react-query";

import { getPanel, panelKey } from "./panel-api";

/** How often a running panel is read again. */
export const PANEL_POLL_MS = 5_000;

/** The latest hiring panel of a document, read again while it runs. */
export function usePanel(documentId: string | undefined) {
  return useQuery({
    queryKey: panelKey(documentId ?? ""),
    queryFn: ({ signal }) => getPanel(documentId as string, signal),
    enabled: documentId !== undefined,
    refetchInterval: (query) =>
      query.state.data?.panel?.status === "running" ? PANEL_POLL_MS : false,
  });
}
