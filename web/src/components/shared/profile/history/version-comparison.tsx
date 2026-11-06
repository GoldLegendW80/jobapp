import { ArrowLeft, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

import type { Profile, SectionName } from "@/features/profile/api/profile-api";
import { useRevisionRestore } from "@/features/profile/api/use-revision-restore";
import { sameData } from "@/features/profile/helpers/diff";
import { SECTION_LABELS } from "@/features/profile/helpers/labels";
import { absoluteTime, relativeTime } from "@/features/profile/helpers/when";
import { FieldDifferences } from "./field-differences";
import {
  ChangedElsewhereDialog,
  DiscardChangesDialog,
} from "./restore-confirmations";
import { TextDifferences } from "./text-differences";

/** The right pane of the History drawer: a revision against the current version, and its restore. */
export function VersionComparison({
  section,
  revision,
  isCurrent,
  profile,
  dirty,
  onBack,
  onRestored,
  onConflict,
}: {
  section: SectionName;
  revision: number;
  isCurrent: boolean;
  profile: Profile;
  dirty: boolean;
  onBack: () => void;
  onRestored: (saved: Profile, from: number) => void;
  /** a 409 put the version saved elsewhere in the cache: the tab must show it */
  onConflict: () => void;
}) {
  const label = SECTION_LABELS[section];
  const [confirming, setConfirming] = useState(false);
  const { version, restoring, restoreVersion, stale, setStale, error } =
    useRevisionRestore({ section, revision, profile, onRestored, onConflict });
  const current = profile.sections[section];
  const unchanged =
    version.data !== undefined && sameData(version.data.data, current);

  return (
    <div className="flex flex-col gap-4">
      <Button
        variant="ghost"
        size="sm"
        className="w-fit md:hidden"
        onClick={onBack}
      >
        <ArrowLeft aria-hidden="true" />
        All versions
      </Button>
      {version.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" />
          Loading revision {revision}
        </p>
      ) : version.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Revision {revision} could not be loaded</AlertTitle>
          <AlertDescription>{describeError(version.error)}</AlertDescription>
        </Alert>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-0.5">
              <h3 className="text-base font-semibold">
                Revision {revision}
                {isCurrent ? " (current)" : ""}
              </h3>
              <p className="text-sm text-muted-foreground">
                Saved {relativeTime(version.data.created_at)},{" "}
                {absoluteTime(version.data.created_at)}
              </p>
            </div>
            <Button
              disabled={isCurrent || unchanged || restoring}
              aria-busy={restoring}
              onClick={() => {
                if (dirty) setConfirming(true);
                else restoreVersion();
              }}
            >
              {restoring ? (
                <Spinner aria-hidden="true" />
              ) : (
                <RotateCcw aria-hidden="true" />
              )}
              {restoring ? "Restoring" : "Restore this version"}
            </Button>
          </div>
          {isCurrent || unchanged ? (
            <p className="text-sm text-muted-foreground">
              {isCurrent
                ? "This is the current version."
                : "This version holds the same data as the current one."}
            </p>
          ) : null}
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {section === "facts" ? (
            <TextDifferences
              revision={revision}
              before={version.data.data}
              current={current}
            />
          ) : (
            <FieldDifferences
              section={section}
              revision={revision}
              before={version.data.data}
              current={current}
            />
          )}
        </>
      )}
      <DiscardChangesDialog
        open={confirming}
        onOpenChange={setConfirming}
        label={label}
        revision={revision}
        onRestore={restoreVersion}
      />
      <ChangedElsewhereDialog
        open={stale}
        onOpenChange={setStale}
        label={label}
        revision={revision}
        onRestore={restoreVersion}
      />
    </div>
  );
}
