import { Pause, X } from "lucide-react";
import { useId } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
import { SiteSignIn } from "@/components/shared/browser/site-signin";
import type { Search } from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";

import type { Sources } from "@/features/jobs/api/use-sources";
import { lineFacts } from "@/features/jobs/helpers/source-line";
import type { SourceRow } from "@/features/jobs/helpers/sources";
import { AdzunaNote } from "./adzuna-note";
import { LinkedInSearchChoice } from "./linkedin-search-choice";
import { RiskNote } from "./risk-note";
import { SourceKindIcon } from "./source-kind-icon";

/** One address of the list: its name, how it is read, a job website's risk sentence and
 * permission, and what can be done with it (search it again, sign in, remove). */
export function SourceLine({
  row,
  sources,
  reviewed,
  disabled,
  onRemove,
  onResume,
}: {
  row: SourceRow;
  sources: Sources;
  reviewed: Search;
  disabled: boolean;
  onRemove: () => void;
  onResume: () => void;
}) {
  const id = useId();
  const { isLinkedIn, how, first, owned, signIn } = lineFacts(
    row,
    sources.draft.sites,
    sources.linkedinMode,
  );
  const note = row.platform
    ? [owned, first ? sources.noteOf(row.platform, reviewed) : null]
        .filter(Boolean)
        .join(" ") || null
    : null;
  return (
    <Item
      asChild
      variant="outline"
      className={cn(
        // The line reaches 0.5rem past the gutter on each side, so its icon sits on the glyph
        // axis and its text at the column's edge; its padding gives back the border's pixel
        // (setup-grid.tsx).
        "-mr-2 -ml-11 grid min-w-0 grid-cols-[2.25rem_minmax(0,1fr)_auto] items-start gap-0 rounded-xl bg-card py-3 pr-[calc(0.5rem-1px)] pl-[calc(0.5rem-1px)] shadow-xs motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-1",
        !row.enabled && "opacity-75",
      )}
    >
      <li>
        <ItemMedia
          aria-hidden="true"
          className="h-7 w-7 group-has-[[data-slot=item-description]]/item:translate-y-0"
        >
          <SourceKindIcon kind={row.kind} className="size-7 rounded-lg" />
        </ItemMedia>
        <ItemContent className="min-w-0 gap-1.5">
          <ItemTitle className="min-h-7 w-auto min-w-0 flex-wrap font-normal">
            <span className="min-w-0 font-medium break-all">{row.name}</span>
            {row.enabled ? null : (
              <Badge variant="outline">
                <Pause aria-hidden="true" />
                Paused
              </Badge>
            )}
          </ItemTitle>
          <ItemDescription className="line-clamp-none text-xs leading-4 text-wrap">
            {how}
          </ItemDescription>
          {row.kind === "website" && row.platform ? (
            <RiskNote
              id={`${id}-risk`}
              platform={row.platform}
              state={sources.permissionOf(row.platform)}
              note={note}
            />
          ) : null}
          {isLinkedIn && first && row.enabled ? (
            <LinkedInSearchChoice
              value={sources.linkedinMode}
              onChange={sources.setLinkedinMode}
            />
          ) : null}
          {row.feed === "adzuna" ? <AdzunaNote /> : null}
          {!row.enabled || signIn ? (
            <div className="flex flex-col items-start gap-2 pt-0.5">
              {row.enabled ? null : (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={onResume}
                >
                  Search it again
                </Button>
              )}
              {/* B17: sign in to this website once, then searches read it signed in. */}
              {signIn ? <SiteSignIn url={row.url} optional /> : null}
            </div>
          ) : null}
        </ItemContent>
        <ItemActions className="-mt-0.5">
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            disabled={disabled}
            aria-label={`Remove ${row.name}`}
            onClick={onRemove}
          >
            <X aria-hidden="true" />
          </Button>
        </ItemActions>
      </li>
    </Item>
  );
}
