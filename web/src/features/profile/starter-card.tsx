/**
 * The starter list in Search options (opportunities O5c, issue #497): the wizard's list of public
 * company boards and the Google Careers and VIE sites (onboarding/starter-boards.tsx, same routes,
 * same rules), for an account that skipped the wizard or wants more boards later. Closed until the
 * person opens it, except when the search has no board and no site yet. The list is loaded only
 * once open.
 */
import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { StarterBoards } from "@/features/onboarding/starter-boards";

import type { Profile } from "./api";
import { Group } from "./section-ui";

export const STARTER_HELD =
  "Save or undo your changes to the search first: the list adds to your saved search.";
export const STARTER_BUSY =
  "Updating your search from the list. The fields below are read only until it is done.";

export function StarterCard({
  profile,
  dirty,
  onChoosing,
  onChanged,
}: {
  profile: Profile;
  /** the tab has unsaved changes: the list waits, so a choice never races a pending save */
  dirty: boolean;
  /** true at the click on a choice, false when it changed nothing or failed */
  onChoosing: (busy: boolean) => void;
  onChanged: () => void | Promise<void>;
}) {
  const search = profile.sections.search;
  const empty =
    search.boards.length === 0 && (search.browser_sources ?? []).length === 0;
  const [open, setOpen] = useState(empty);
  const id = useId();
  return (
    <Group
      title="Suggested boards and sites"
      description="Public company boards and job sites picked from your search targets, the same list the setup offers."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          onClick={() => {
            setOpen((was) => !was);
          }}
        >
          {open ? (
            <ChevronUp aria-hidden="true" />
          ) : (
            <ChevronDown aria-hidden="true" />
          )}
          {open ? "Hide the list" : "Show the list"}
        </Button>
      }
    >
      <div id={`${id}-list`} hidden={!open}>
        {open ? (
          <StarterBoards
            place="search"
            held={dirty ? STARTER_HELD : undefined}
            onChoosing={onChoosing}
            onChanged={onChanged}
          />
        ) : null}
      </div>
    </Group>
  );
}
