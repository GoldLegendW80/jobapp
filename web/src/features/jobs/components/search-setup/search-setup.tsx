import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useProfile } from "@/features/profile/api/use-profile";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useRefreshPermissions } from "@/features/jobs/api/use-sources";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { COLUMN } from "@/components/shared/jobs/setup-grid";
import { SetupForm } from "./setup-form";
import { BODY, SetupSurface } from "./setup-surface";

/**
 * Find jobs: where to search (one list of addresses, sources/sources-editor.tsx) and what to look
 * for, then Save and start search. Saving records the permission of each known job website in the
 * list (use-sources.ts), then the search starts on the saved revision. A bottom sheet on a
 * phone, a dialog above; its words and fields keep one edge (setup-grid.tsx).
 */
export function SearchSetup({
  runner,
  onClose,
  onReturnFocus,
}: {
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
}) {
  const profile = useProfile({ retry: false });
  const refreshPermissions = useRefreshPermissions();
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  return (
    <SetupSurface
      onClose={() => {
        if (!runner.starting && !busy) onClose();
      }}
      onReturnFocus={onReturnFocus}
    >
      {profile.isPending ? (
        <div className={cn(BODY, COLUMN)}>
          <p role="status" className="text-sm">
            Loading your search settings…
          </p>
        </div>
      ) : profile.isError ? (
        <div className={cn(BODY, COLUMN)}>
          <div role="alert" className="flex flex-col items-start gap-3 text-sm">
            <p>
              Your search settings could not be loaded.{" "}
              {describeError(profile.error)}
            </p>
            <Button variant="outline" onClick={() => void profile.refetch()}>
              Try again
            </Button>
          </div>
        </div>
      ) : (
        <SetupForm
          key={generation}
          profile={profile.data}
          runner={runner}
          onClose={onClose}
          onBusyChange={setBusy}
          onReload={async () => {
            refreshPermissions();
            const current = await profile.refetch();
            if (current.isSuccess) setGeneration((value) => value + 1);
          }}
        />
      )}
    </SetupSurface>
  );
}
