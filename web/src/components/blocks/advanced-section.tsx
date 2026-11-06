/**
 * "Advanced": the options most people leave as they are, folded away under one button until
 * opened. One component for every page that has such options, so they open and read the same way.
 *
 * Collapsed by default. Give `pageKey` and the open state is kept in local storage under that key
 * (a blocked storage only means it starts collapsed). Children are rendered only while open; pass
 * `keepMounted` for a form whose fields must stay registered while folded. A field inside with an
 * error is named by `errorFieldId`: the section opens and that field takes the focus, and it
 * cannot be closed while `errorFieldId` is set. Pass `open` and `onOpenChange` instead to hold
 * the state from outside.
 *
 * Built on shadcn's Collapsible. Its region stays in the page while folded (hidden), so the
 * button always names the region it controls; the children are left out unless `keepMounted`.
 */
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
    <Collapsible
      open={shown}
      onOpenChange={(next) => {
        setOwn(next);
        writeStored(pageKey, next);
        onOpenChange?.(next);
      }}
      className={cn("flex flex-col gap-4", className)}
    >
      <CollapsibleTrigger asChild aria-controls={id}>
        <Button
          variant="link"
          // A form with an icon gutter (jobs/setup-grid.tsx) sets the two properties, so the
          // chevron hangs in its gutter and the title starts at its text edge; else 0 and 0.25rem.
          className="ms-[calc(-1*var(--advanced-hang,0px))] h-auto min-h-11 w-fit justify-start gap-(--advanced-gap,0.25rem) p-0 text-left whitespace-normal has-[>svg]:px-0 sm:min-h-0"
        >
          <ChevronDown
            aria-hidden="true"
            className={cn(
              "size-4 transition-transform motion-reduce:transition-none",
              shown && "rotate-180",
            )}
          />
          {title}
        </Button>
      </CollapsibleTrigger>
      {/* Mounted while folded too (hidden), so `aria-controls` always names it. */}
      <CollapsibleContent
        forceMount
        id={id}
        role="region"
        aria-label={title}
        hidden={!shown}
        className="flex flex-col gap-4"
      >
        {shown || keepMounted ? children : null}
      </CollapsibleContent>
    </Collapsible>
  );
}
