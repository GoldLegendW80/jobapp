import { Radio } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import type { Connection } from "@/features/agents/api/runs-api";

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  ended: "Ended",
};

/** The state of the live connection to a run's steps. */
export function ConnectionState({ state }: { state: Connection }) {
  return (
    <span
      role="status"
      data-connection={state}
      className={cn(
        "inline-flex items-center gap-1.5 text-xs text-muted-foreground",
        state === "reconnecting" && "text-warning",
      )}
    >
      {state === "live" ? (
        <Radio aria-hidden="true" className="size-3.5 text-success" />
      ) : state === "ended" ? null : (
        <Spinner aria-hidden="true" role={undefined} className="size-3.5" />
      )}
      {CONNECTION_LABEL[state]}
    </span>
  );
}
