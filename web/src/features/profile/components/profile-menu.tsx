import { Download, History, MoreHorizontal, Upload } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";
import { HistoryDrawer } from "@/components/shared/profile/history-drawer";

import { useRefreshAbout } from "../api/about-api";
import { SECTIONS, type Profile, type SectionName } from "../api/profile-api";
import { useExportProfile } from "../api/use-profile-file";
import { SECTION_LABELS } from "../helpers/labels";
import { ImportDialog } from "./profile-file";

/**
 * More actions of the profile page: the History of each section (view and restore past saves),
 * Export and Import. A restore changes one section and an import all of them: `onChanged` names
 * the section restored (none after an import) so the page reloads its forms, and About you is
 * read again. `dirty` lists the sections with unsaved changes, which both ask about first.
 */
export function ProfileMenu({
  profile,
  dirty,
  onChanged,
}: {
  profile: Profile;
  dirty: readonly SectionName[];
  onChanged: (section?: SectionName) => void;
}) {
  const [history, setHistory] = useState<SectionName>();
  const [importing, setImporting] = useState(false);
  const exporting = useExportProfile();
  const refreshAbout = useRefreshAbout();
  const changed = (section?: SectionName) => {
    refreshAbout();
    onChanged(section);
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            aria-label="More actions"
            aria-busy={exporting.isPending}
          >
            {exporting.isPending ? (
              <Spinner aria-hidden="true" />
            ) : (
              <MoreHorizontal aria-hidden="true" />
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <History aria-hidden="true" />
              History
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {SECTIONS.filter((name) => name !== "preferences").map((name) => (
                <DropdownMenuItem
                  key={name}
                  onSelect={() => {
                    setHistory(name);
                  }}
                >
                  {SECTION_LABELS[name]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuItem
            onSelect={() => {
              exporting.mutate();
            }}
          >
            <Download aria-hidden="true" />
            Export profile
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setImporting(true);
            }}
          >
            <Upload aria-hidden="true" />
            Import profile
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <HistoryDrawer
        section={history ?? "identity"}
        profile={profile}
        dirty={history !== undefined && dirty.includes(history)}
        open={history !== undefined}
        onOpenChange={(open) => {
          if (!open) setHistory(undefined);
        }}
        onRestored={() => {
          changed(history);
        }}
      />
      <ImportDialog
        open={importing}
        onOpenChange={setImporting}
        profile={profile}
        dirtySection={dirty[0]}
        onImported={() => {
          changed();
        }}
      />
    </>
  );
}
