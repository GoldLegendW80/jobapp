/**
 * "Advanced": the options most people leave as they are, folded away under one button until
 * opened. One component for every page that has such options, so they open and read the same way.
 *
 * Collapsed by default. Give `pageKey` and the open state is kept in local storage under that key
 * (a blocked storage only means it starts collapsed). Children are rendered only while open; pass
 * `keepMounted` for a form whose fields must stay registered while folded. A field inside with an
 * error is named by `errorFieldId`: the section opens and that field takes the focus. Pass `open`
 * and `onOpenChange` instead to hold the state from outside.
 */
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const STORAGE_PREFIX = "advanced:";

function readStored(pageKey: string | undefined): boolean {
  if (!pageKey) return false;
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + pageKey) === "1";
  } catch {
    return false;
  }
}

function writeStored(pageKey: string | undefined, open: boolean) {
  if (!pageKey) return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + pageKey, open ? "1" : "0");
  } catch {
    // Storage blocked or full: the choice only lasts for this visit.
  }
}

export function AdvancedSection({
  title = "Advanced",
  pageKey,
  open,
  onOpenChange,
  errorFieldId,
  keepMounted = false,
  className,
  children,
}: {
  title?: string;
  pageKey?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  errorFieldId?: string;
  keepMounted?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const [own, setOwn] = useState(() => readStored(pageKey));
  const shown = errorFieldId !== undefined || (open ?? own);
  const id = useId();

  useEffect(() => {
    if (errorFieldId === undefined) return;
    document.getElementById(errorFieldId)?.focus();
  }, [errorFieldId]);

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <button
        type="button"
        aria-expanded={shown}
        aria-controls={id}
        className="flex min-h-11 w-fit items-center gap-1 rounded-md text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none sm:min-h-0"
        onClick={() => {
          const next = !shown;
          setOwn(next);
          writeStored(pageKey, next);
          onOpenChange?.(next);
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
        {shown || keepMounted ? children : null}
      </div>
    </div>
  );
}
