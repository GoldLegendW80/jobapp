import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Profile } from "@/features/profile/api/profile-api";
import { useProfile } from "@/features/profile/api/use-profile";
import {
  useSearchSetup,
  type SetupSnapshot,
  type SetupTarget,
} from "@/features/jobs/hooks/use-search-setup";
import {
  MAIN_SEARCH,
  type SavedSearch,
} from "@/features/jobs/api/searches-api";
import { useSavedSearches } from "@/features/jobs/api/use-saved-searches";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useRefreshPermissions } from "@/features/jobs/api/use-sources";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import type { FindJobsScreen } from "@/features/jobs/hooks/use-find-jobs";
import { COLUMN } from "@/components/shared/jobs/setup-grid";
import { SearchesList } from "./searches-list";
import { SetupForm } from "./setup-form";
import { BODY, SetupSurface } from "./setup-surface";

const LIST_INTRO =
  "Run a search now, change it, or add another. Each one keeps its own websites, titles and places.";
const FORM_INTRO =
  "Add the places to search, say what you want, and save or start. The app keeps the postings that match.";

/**
 * Find jobs: Your searches first (searches-list.tsx), then the form of the search the person
 * opens: where to search (one list of addresses, sources/sources-editor.tsx) and what to look
 * for, with Save and "Save and start search". Saving records the permission of each known job
 * website in the list (use-sources.ts). A drawer from the bottom on a phone, a dialog above; its
 * words and fields keep one edge (setup-grid.tsx). Each search keeps its own draft until Find
 * jobs closes (#798).
 */
export function SearchSetup({
  runner,
  onClose,
  onReturnFocus,
  initial = { kind: "list" },
}: {
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
  initial?: FindJobsScreen;
}) {
  const [screen, setScreen] = useState<FindJobsScreen>(initial);
  const [drafts, setDrafts] = useState<ReadonlyMap<string, SetupSnapshot>>(
    () => new Map(),
  );
  const keep = (id: string, snapshot?: SetupSnapshot) => {
    setDrafts((old) => {
      const next = new Map(old);
      if (snapshot) next.set(id, snapshot);
      else next.delete(id);
      return next;
    });
  };
  const profile = useProfile({ retry: false });
  const searches = useSavedSearches();
  const refreshPermissions = useRefreshPermissions();
  const [generation, setGeneration] = useState(0);
  // A new search's id once its form wrote it: its draft and its reload are that search's.
  const [created, setCreated] = useState<string | null>(null);
  const toList = () => {
    setCreated(null);
    setScreen({ kind: "list" });
  };

  if (screen.kind === "list")
    return (
      <SetupSurface
        onClose={onClose}
        onReturnFocus={onReturnFocus}
        description={LIST_INTRO}
      >
        <SearchesList
          runner={runner}
          onEdit={(id) => {
            setScreen({ kind: "edit", id });
          }}
          onNew={() => {
            setScreen({ kind: "edit", id: "" });
          }}
          onStarted={onClose}
        />
      </SetupSurface>
    );

  const saved =
    screen.id && screen.id !== MAIN_SEARCH
      ? searches.data?.items.find((item) => item.id === screen.id)
      : undefined;
  const waiting =
    profile.isPending ||
    profile.isError ||
    (screen.id !== MAIN_SEARCH && (searches.isPending || searches.isError));
  if (waiting || (screen.id && screen.id !== MAIN_SEARCH && !saved))
    return (
      <SetupSurface
        onClose={onClose}
        onReturnFocus={onReturnFocus}
        description={FORM_INTRO}
      >
        <div className={cn(BODY, COLUMN)}>
          {profile.isPending || searches.isPending ? (
            <p role="status" className="text-sm">
              Loading your search settings…
            </p>
          ) : (
            <div
              role="alert"
              className="flex flex-col items-start gap-3 text-sm"
            >
              <p>
                {profile.isError || searches.isError
                  ? `Your search settings could not be loaded. ${describeError(profile.error ?? searches.error)}`
                  : "This search no longer exists."}
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  if (profile.isError) void profile.refetch();
                  if (searches.isError) void searches.refetch();
                  if (!profile.isError && !searches.isError) toList();
                }}
              >
                {profile.isError || searches.isError
                  ? "Try again"
                  : "Back to your searches"}
              </Button>
            </div>
          )}
        </div>
      </SetupSurface>
    );

  const target: SetupTarget = !screen.id
    ? { kind: "new" }
    : saved
      ? { kind: "saved", search: saved }
      : { kind: "main" };
  const id = screen.id || (created ?? "");
  return (
    <LoadedSetup
      key={`${screen.id}:${String(generation)}`}
      profile={profile.data}
      target={target}
      names={(searches.data?.items ?? [])
        .filter((item) => !item.main && item.id !== id)
        .map((item) => item.name)}
      initial={drafts.get(screen.id)}
      runner={runner}
      onClose={onClose}
      onReturnFocus={onReturnFocus}
      onBack={(snapshot) => {
        keep(screen.id);
        keep(id, snapshot);
        toList();
      }}
      onSaved={() => {
        keep(screen.id);
        keep(id);
        toList();
      }}
      onWritten={(stored) => {
        if (!screen.id) setCreated(stored.id);
      }}
      onReload={async () => {
        refreshPermissions();
        keep(screen.id);
        keep(id);
        const [current] = await Promise.all([
          profile.refetch(),
          searches.refetch(),
        ]);
        if (created) {
          setCreated(null);
          setScreen({ kind: "edit", id: created });
        }
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
  target,
  names,
  initial,
  runner,
  onClose,
  onReturnFocus,
  onBack,
  onSaved,
  onWritten,
  onReload,
}: {
  profile: Profile;
  target: SetupTarget;
  names: readonly string[];
  initial: SetupSnapshot | undefined;
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
  onBack: (snapshot: SetupSnapshot) => void;
  onSaved: () => void;
  onWritten: (saved: SavedSearch) => void;
  onReload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const setup = useSearchSetup({
    profile,
    runner,
    target,
    names,
    initial,
    onClose,
    onSaved,
    onWritten,
    onBusyChange: setBusy,
  });
  const name =
    target.kind === "main"
      ? "Main search"
      : target.kind === "saved"
        ? target.search.name
        : "New search";
  return (
    <SetupSurface
      onClose={onClose}
      dismissible={!runner.starting && !busy}
      onReturnFocus={onReturnFocus}
      description={`${name}: ${FORM_INTRO}`}
    >
      <SetupForm
        setup={setup}
        onClose={onClose}
        onReload={onReload}
        onBack={() => {
          onBack(setup.snapshot());
        }}
      />
    </SetupSurface>
  );
}
