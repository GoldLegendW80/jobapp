import { useId, type ReactNode } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import type { ListSelection } from "@/features/jobs/helpers/selection";

/** The white card around the list, with the box that ticks every posting of the view on top. */
export function ListCard({
  selection,
  className,
  children,
}: {
  selection?: ListSelection;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-xl bg-card text-card-foreground",
        className,
      )}
    >
      {selection ? <SelectAll selection={selection} /> : null}
      {children}
    </div>
  );
}

function SelectAll({ selection }: { selection: ListSelection }) {
  const id = useId();
  return (
    <div className="flex items-center gap-3 border-b px-5 py-3">
      <Checkbox
        id={id}
        className="cursor-pointer"
        checked={
          selection.allState === "all"
            ? true
            : selection.allState === "some"
              ? "indeterminate"
              : false
        }
        onCheckedChange={selection.toggleAll}
      />
      <Label
        htmlFor={id}
        className="cursor-pointer font-normal text-muted-foreground"
      >
        {selection.allState === "none"
          ? "Select every posting in this view"
          : "Clear the selection"}
      </Label>
    </div>
  );
}
