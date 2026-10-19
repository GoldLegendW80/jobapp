/**
 * "Advanced": the options most people leave as they are, folded away under one button until
 * opened. One component for the pages that have such options, so they open and read the same
 * way. Pass `open` and `onOpenChange` to hold it open from outside (a field inside with an error
 * to show); the content stays mounted while folded, so a form keeps its fields.
 */
import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function AdvancedSection({
  title = "Advanced",
  open,
  onOpenChange,
  className,
  children,
}: {
  title?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(false);
  const shown = open ?? own;
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <button
        type="button"
        aria-expanded={shown}
        aria-controls={id}
        className="flex w-fit items-center gap-1 rounded-md text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
        onClick={() => {
          setOwn(!shown);
          onOpenChange?.(!shown);
        }}
      >
        <ChevronDown
          aria-hidden="true"
          className={cn("size-4 transition-transform", shown && "rotate-180")}
        />
        {title}
      </button>
      <div
        id={id}
        role="region"
        aria-label={title}
        hidden={!shown}
        className="flex flex-col gap-4"
      >
        {children}
      </div>
    </div>
  );
}
