import type { Refused } from "@/features/ingestion/api/refused-schema";
import { refusalLine } from "@/features/ingestion/helpers/refusal-words";

/**
 * Why proposals of a finished run were not kept: one quiet line per section and rule, under the
 * run's figures and at the head of its review. Nothing when the checks kept everything.
 */
export function RunRefusals({ refused }: { refused: readonly Refused[] }) {
  if (refused.length === 0) return null;
  return (
    <ul
      aria-label="What was not kept"
      className="flex flex-col gap-1 text-sm text-muted-foreground"
      data-testid="run-refusals"
    >
      {refused.map((one) => (
        <li key={`${one.section}:${one.rule}`}>{refusalLine(one)}</li>
      ))}
    </ul>
  );
}
