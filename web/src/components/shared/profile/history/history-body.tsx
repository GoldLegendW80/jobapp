import { useState } from "react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

import type { Profile, SectionName } from "@/features/profile/api/profile-api";
import { useSectionVersions } from "@/features/profile/api/use-section-versions";
import { SECTION_LABELS } from "@/features/profile/helpers/labels";
import { VersionComparison } from "./version-comparison";
import { VersionList } from "./version-list";

/** The open History drawer: the list of versions, and the one chosen beside it (one at a time on a phone). */
export function HistoryBody({
  section,
  profile,
  dirty,
  onRestored,
}: {
  section: SectionName;
  profile: Profile;
  dirty: boolean;
  onRestored: () => void;
}) {
  const label = SECTION_LABELS[section];
  const [selected, setSelected] = useState<number>();
  const versions = useSectionVersions(section);

  return (
    <div className="grid min-h-0 flex-1 md:grid-cols-[14rem_1fr]">
      <VersionList
        label={label}
        versions={versions}
        selected={selected}
        onSelect={setSelected}
      />
      <div
        className={cn(
          "min-h-0 overflow-y-auto p-4",
          selected === undefined && "hidden md:block",
        )}
      >
        {selected === undefined ? (
          versions.items.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              Choose a version to compare it with the current one.
            </p>
          ) : null
        ) : (
          <VersionComparison
            key={selected}
            section={section}
            revision={selected}
            isCurrent={selected === versions.newest}
            profile={profile}
            dirty={dirty}
            onBack={() => {
              setSelected(undefined);
            }}
            onConflict={onRestored}
            onRestored={(saved, from) => {
              onRestored();
              setSelected(undefined);
              toast.success(
                `Revision ${String(from)} of ${label.toLowerCase()} restored as revision ${String(saved.revision)}.`,
              );
            }}
          />
        )}
      </div>
    </div>
  );
}
