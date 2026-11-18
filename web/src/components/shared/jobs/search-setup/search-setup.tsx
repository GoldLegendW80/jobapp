import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Profile } from "@/features/profile/api/profile-api";
import { useProfile } from "@/features/profile/api/use-profile";
import { useSearchSetup } from "@/features/jobs/hooks/use-search-setup";
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
 * list (use-sources.ts), then the search starts on the saved revision. A drawer from the bottom on a
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
  if (profile.isPending || profile.isError)
    return (
      <SetupSurface onClose={onClose} onReturnFocus={onReturnFocus}>
        {profile.isPending ? (
          <div className={cn(BODY, COLUMN)}>
            <p role="status" className="text-sm">
              Loading your search settings…
            </p>
          </div>
        ) : (
          <div className={cn(BODY, COLUMN)}>
            <div
              role="alert"
              className="flex flex-col items-start gap-3 text-sm"
            >
              <p>
                Your search settings could not be loaded.{" "}
                {describeError(profile.error)}
              </p>
              <Button variant="outline" onClick={() => void profile.refetch()}>
                Try again
              </Button>
            </div>
          </div>
        )}
      </SetupSurface>
    );
  return (
    <LoadedSetup
      key={generation}
      profile={profile.data}
      runner={runner}
      onClose={onClose}
      onReturnFocus={onReturnFocus}
      onReload={async () => {
        refreshPermissions();
        const current = await profile.refetch();
        if (current.isSuccess) setGeneration((value) => value + 1);
      }}
    />
  );
}

/**
 * The draft lives here, above the surface: a change of screen size swaps the drawer for the dialog
 * (setup-surface.tsx), which remounts everything inside it, so the draft kept below would be lost.
 */
function LoadedSetup({
  profile,
  runner,
  onClose,
  onReturnFocus,
  onReload,
}: {
  profile: Profile;
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
  onReload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const setup = useSearchSetup({
    profile,
    runner,
    onClose,
    onBusyChange: setBusy,
  });
  return (
    <SetupSurface
      onClose={onClose}
      dismissible={!runner.starting && !busy}
      onReturnFocus={onReturnFocus}
    >
      <SetupForm setup={setup} onClose={onClose} onReload={onReload} />
    </SetupSurface>
  );
}
