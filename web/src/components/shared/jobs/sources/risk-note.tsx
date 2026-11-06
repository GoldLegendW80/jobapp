import type { UseQueryResult } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

import type {
  Permission,
  Protected,
} from "@/features/jobs/helpers/site-permissions";
import { PLATFORM_NAMES } from "@/features/jobs/helpers/sources";
import { Glyph, HANG } from "../setup-grid";

// LinkedIn keeps one switch for its search, its reader and Easy Apply (owner, 2026-10-27), so
// its line says what else adding it allows. The server's sentence and its version stay as they are.
const ALSO_ALLOWS: Partial<Record<Protected, string>> = {
  linkedin:
    "Adding LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent.",
};

/** A job website's risk sentence as the server words it now, what else adding it allows, and
 * what saving does. */
export function RiskNote({
  id,
  platform,
  state,
  note,
}: {
  id: string;
  platform: Protected;
  state: UseQueryResult<Permission>;
  note: string | null;
}) {
  const label = PLATFORM_NAMES[platform];
  const also = ALSO_ALLOWS[platform];
  return (
    // From the gutter: its sign on the glyph axis, its words at the column's edge.
    <div
      id={id}
      className={cn(
        HANG,
        "rounded-lg bg-warning-muted py-2 pr-3 text-xs text-foreground",
      )}
    >
      <Glyph line="h-4">
        <TriangleAlert className="size-3.5" />
      </Glyph>
      <div className="flex flex-col gap-1">
        {state.isPending ? (
          <p>Loading the {label} risk sentence…</p>
        ) : state.isError ? (
          <p className="text-destructive">
            The {label} risk sentence could not be loaded, so its permission
            cannot be given now.
          </p>
        ) : (
          <p>{state.data.text}</p>
        )}
        {also ? <p>{also}</p> : null}
        {note && state.isSuccess ? <p className="font-medium">{note}</p> : null}
      </div>
    </div>
  );
}
