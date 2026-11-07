import { History } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import type { Profile, SectionName } from "@/features/profile/api/profile-api";
import { SECTION_LABELS } from "@/features/profile/helpers/labels";
import { HistoryBody } from "./history/history-body";

/**
 * The History of a section, a drawer with its own button (Settings, Search) or opened from a menu
 * (the profile page): the section's saves, newest first, 20 at a time; choosing one compares it
 * with the current version, and "Restore this version" saves its data as a new revision (PUT
 * with the page's revision, so a save made elsewhere meanwhile is a 409, never overwritten
 * silently). With unsaved changes in the section's form, restoring asks first. `onRestored` lets
 * the page reload that form from the new current version.
 */
export function HistoryDrawer({
  section,
  profile,
  dirty,
  onRestored,
  open: given,
  onOpenChange,
}: {
  section: SectionName;
  profile: Profile;
  dirty: boolean;
  onRestored: () => void;
  /** Opened from elsewhere (the profile page's More actions): no button of its own. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [own, setOwn] = useState(false);
  const open = given ?? own;
  const setOpen = onOpenChange ?? setOwn;
  const label = SECTION_LABELS[section];
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      {given === undefined ? (
        <SheetTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={`History of ${label.toLowerCase()}`}
          >
            <History aria-hidden="true" />
            History
          </Button>
        </SheetTrigger>
      ) : null}
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-3xl">
        <SheetHeader className="border-b">
          <SheetTitle>{label} history</SheetTitle>
          <SheetDescription>
            Every save of {label.toLowerCase()} is kept. Choose one to compare
            it with the current version and restore it.
          </SheetDescription>
        </SheetHeader>
        {open ? (
          <HistoryBody
            section={section}
            profile={profile}
            dirty={dirty}
            onRestored={onRestored}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
