/**
 * The hiring panel's outcome in one sentence (documents D29), shared by the job page's row and
 * the document's section.
 */
import type { Panel } from "../api/panel-api";

/** The screen's outcome in a few words: "screen PASS, was REJECT". */
function screenWords(panel: Panel): string | null {
  const { screen_before: before, screen_after: after } = panel;
  if (after !== null && before !== null && after !== before)
    return `screen ${after}, was ${before}`;
  if (after !== null) return `screen ${after}`;
  if (before !== null) return `screen ${before}`;
  return null;
}

/** The card's one line: "Hiring panel: screen PASS, was REJECT; 4 of 5 readers would hire". */
export function panelSentence(panel: Panel): string {
  if (panel.status === "running")
    return "Hiring panel: seven reviewers are reading it";
  const parts = [screenWords(panel)];
  if (panel.readers_answered > 0)
    parts.push(
      `${String(panel.readers_hire)} of ${String(panel.readers_answered)} readers would hire`,
    );
  const said = parts.filter((part): part is string => part !== null);
  if (said.length === 0) return "Hiring panel: no reviewer could read it";
  return `Hiring panel: ${said.join("; ")}`;
}
