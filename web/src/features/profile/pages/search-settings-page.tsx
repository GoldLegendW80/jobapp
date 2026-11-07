import { useCallback, useState } from "react";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { PageBody } from "@/components/blocks/page";
import { useTitle } from "@/hooks/use-title";

import { useProfile } from "../api/use-profile";
import { HistoryDrawer } from "@/components/shared/profile/history-drawer";
import { SearchTab } from "../components/search-tab";
import { SettingsFrame } from "@/components/shared/profile/settings-frame";
import { UnsavedGuard } from "@/components/shared/profile/unsaved-guard";

/**
 * /settings/search, Settings, Search (owner, 2026-11-05: "search setting should be in the
 * settings of the user"): the four word lists with the job title ideas and the place chip,
 * contracts, remote stance, what the person aims for, minimum fit, posting age, where the search
 * runs, the never apply rules, the documents and the preferences, with their save bars. It saves
 * the profile's `search` and `preferences` sections; /profile?tab=search comes here.
 */
export function SearchSettingsPage() {
  useTitle("Search settings");
  const query = useProfile();
  const [dirty, setDirty] = useState(false);
  const [generation, setGeneration] = useState(0);
  const onDirtyChange = useCallback((value: boolean) => {
    setDirty(value);
  }, []);

  return (
    <SettingsFrame
      actions={
        query.data ? (
          <HistoryDrawer
            section="search"
            profile={query.data}
            dirty={dirty}
            onRestored={() => {
              setDirty(false);
              setGeneration((value) => value + 1);
            }}
          />
        ) : null
      }
    >
      {query.isPending ? (
        <LoadingCard>Loading your settings</LoadingCard>
      ) : query.isError ? (
        <LoadError query={query}>Your settings could not be loaded.</LoadError>
      ) : (
        <PageBody>
          <SearchTab
            key={generation}
            profile={query.data}
            onDirtyChange={onDirtyChange}
          />
        </PageBody>
      )}
      <UnsavedGuard
        dirty={dirty}
        what="Search settings"
        onDiscard={() => {
          setDirty(false);
          setGeneration((value) => value + 1);
        }}
      />
    </SettingsFrame>
  );
}
