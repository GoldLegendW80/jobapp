import { Minus, Plus } from "lucide-react";

import { diffLines } from "@/features/profile/helpers/diff";
import { cn } from "@/lib/utils";

/** The lines added and removed between two prompts, or that they are the same. */
export function PromptDiff({
  from,
  to,
  title = "Changes from the code's prompt",
}: {
  from: string;
  to: string;
  title?: string;
}) {
  if (from === to)
    return (
      <p className="text-sm text-muted-foreground">
        The same as the code&apos;s prompt.
      </p>
    );
  const ops = diffLines(from, to).filter((op) => op.kind !== "same");
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <h3 className="text-sm font-medium">{title}</h3>
      <ul
        aria-label={title}
        className="max-h-96 overflow-auto rounded-md border font-mono text-xs"
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
              ) : (
                <Minus className="mt-1 size-3" />
              )}
            </span>
            <span className="sr-only">
              {op.kind === "added" ? "Added: " : "Removed: "}
            </span>
            {op.text || " "}
          </li>
        ))}
      </ul>
    </div>
  );
}
