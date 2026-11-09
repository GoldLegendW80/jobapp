import type { AgentFlow } from "../api/agents-api";

/** Each arrow in words, for a screen reader: "From Sweep to posting_reader: then reads its postings." */
export function flowSentences(flow: AgentFlow): string[] {
  const byId = new Map(flow.nodes.map((node) => [node.id, node]));
  return flow.edges.flatMap((edge) => {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    if (!from || !to) return [];
    const back = to.column < from.column ? "back " : "";
    return [`From ${from.label} ${back}to ${to.label}: ${edge.label}.`];
  });
}
