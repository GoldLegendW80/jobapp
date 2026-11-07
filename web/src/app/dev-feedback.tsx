import { Agentation } from "agentation";
import { useLocation } from "react-router";

/**
 * Agentation's toolbar, in development only: click an element, write a note, and the note
 * reaches Claude Code through the local agentation MCP server (`npx -y agentation-mcp server`,
 * port 4747). Without that server running, notes are still copied to the clipboard.
 */
export function DevFeedback() {
  // React Router changes the address without a browser event: a render per location keeps the
  // toolbar's notes with the page they were written on.
  useLocation();
  return <Agentation endpoint="http://localhost:4747" appName="jobapp" />;
}
