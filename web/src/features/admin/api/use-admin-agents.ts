import { useQuery } from "@tanstack/react-query";

import { agentKey, agentsKey, listAgents, readAgent } from "./agents-api";

/** Every agent of the app, by category, as the admin sees them. */
export function useAdminAgents() {
  return useQuery({
    queryKey: agentsKey,
    queryFn: ({ signal }) => listAgents(signal),
  });
}

/** One agent: everything that makes it, and its editable parts. */
export function useAdminAgent(name: string) {
  return useQuery({
    queryKey: agentKey(name),
    queryFn: ({ signal }) => readAgent(name, signal),
  });
}
