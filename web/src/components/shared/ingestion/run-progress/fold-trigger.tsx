import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { CollapsibleTrigger } from "@/components/ui/collapsible";

/** The title of a fold: the whole first line of its box, a chevron that turns as it opens. */
export function FoldTrigger({ children }: { children: ReactNode }) {
  return (
    <CollapsibleTrigger asChild>
      <Button
        variant="ghost"
        className="group/fold h-auto w-full justify-start gap-1.5 rounded-lg px-3 py-3 text-left font-medium whitespace-normal has-[>svg]:px-3"
      >
        <ChevronRight
          aria-hidden="true"
          className="transition-transform group-data-[state=open]/fold:rotate-90 motion-reduce:transition-none"
        />
        {children}
      </Button>
    </CollapsibleTrigger>
  );
}
