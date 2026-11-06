import { Minus, Plus } from "lucide-react";

import { cn } from "@/lib/utils";

import { diffLines, factsText } from "@/features/profile/helpers/diff";

/** The fact base of a revision against the current one, line by line. */
export function TextDifferences({
  revision,
  before,
  current,
}: {
  revision: number;
  before: unknown;
  current: unknown;
}) {
  const ops = diffLines(factsText(current), factsText(before));
  const added = ops.filter((op) => op.kind === "added").length;
  const removed = ops.filter((op) => op.kind === "removed").length;
  return (
    <section aria-label="Differences" className="flex flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        {added === 0 && removed === 0
          ? "No difference with the current version."
          : `${String(added)} ${added === 1 ? "line" : "lines"} only in revision ${String(revision)}, ${String(removed)} only in the current version.`}
      </p>
      <ol
        aria-label="Lines of the fact base"
        className="overflow-x-auto rounded-md border font-mono text-xs leading-relaxed"
      >
        {ops.map((op, index) => (
          <li
            key={index}
            data-kind={op.kind}
            className={cn(
              "flex gap-2 px-2 whitespace-pre-wrap [overflow-wrap:anywhere]",
              op.kind === "added" && "bg-success/10 text-foreground",
              op.kind === "removed" && "bg-destructive/10 text-foreground",
            )}
          >
            <span aria-hidden="true" className="w-3 shrink-0 select-none">
              {op.kind === "added" ? (
                <Plus className="mt-1 size-3" />
              ) : op.kind === "removed" ? (
                <Minus className="mt-1 size-3" />
              ) : null}
            </span>
            {op.kind === "added" ? (
              <span className="sr-only">Only in revision {revision}: </span>
            ) : op.kind === "removed" ? (
              <span className="sr-only">Only in the current version: </span>
            ) : null}
            <span>{op.text === "" ? " " : op.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
