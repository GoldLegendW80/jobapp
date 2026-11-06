import { RotateCw } from "lucide-react";
import { useCallback, useState } from "react";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { useProfile } from "../api/use-profile";
import { HistoryDrawer } from "@/components/shared/profile/history-drawer";
import { SearchTab } from "../components/search-tab";
import { SettingsTabs } from "@/components/shared/profile/settings-tabs";
import { UnsavedGuard } from "@/components/shared/profile/unsaved-guard";

/**
 * /settings/search, "Search settings" (owner, 2026-11-05: "search setting should be in the
 * settings of the user"): the profile's Search tab as it was, the four word lists with the job
 * title ideas and the place chip, contracts, remote stance, minimum fit, posting age, the never
 * apply rules, what the person aims for and the documents cards, with their save bars. It still
 * saves the profile's `search` and `preferences` sections; /profile?tab=search comes here.
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
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <PageHeader
        title="Search settings"
        description="What the search keeps and leaves out, and when documents are written for a posting."
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
      />
      <SettingsTabs />
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" />
          Loading your settings
        </p>
      ) : query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Your settings could not be loaded</AlertTitle>
          <AlertDescription>{describeError(query.error)}</AlertDescription>
          <Button
            variant="outline"
            size="sm"
            className="mt-2 w-fit"
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </Alert>
      ) : (
        <SearchTab
          key={generation}
          profile={query.data}
          onDirtyChange={onDirtyChange}
        />
      )}
      <UnsavedGuard
        dirty={dirty}
        what="Search settings"
        onDiscard={() => {
          setDirty(false);
          setGeneration((value) => value + 1);
        }}
      />
    </div>
  );
}
