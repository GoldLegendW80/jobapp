import { EllipsisVertical, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { RescoreDialog } from "./rescore-dialog";

/** The page's "More actions" menu: rescore all postings, or the selected ones. */
export function PageMenu({ selected }: { selected: string[] }) {
  const [target, setTarget] = useState<"all" | "selected" | null>(null);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="More actions">
            <EllipsisVertical aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => {
              setTarget("all");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore all postings
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={selected.length === 0}
            onSelect={() => {
              setTarget("selected");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore the selected postings
            {selected.length ? ` (${selected.length})` : ""}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {target ? (
        <RescoreDialog
          ids={target === "selected" ? selected : undefined}
          onClose={() => {
            setTarget(null);
          }}
        />
      ) : null}
    </>
  );
}
