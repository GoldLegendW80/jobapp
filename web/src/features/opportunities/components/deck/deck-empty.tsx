import { Layers, PartyPopper, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { useJobCount } from "@/features/jobs/api/use-jobs";
import { useProfile } from "@/features/profile/api/use-profile";
import {
  DECK_NO_SKILLS_HINT,
  WORDING,
  deckEmpty,
  deckNoSkills,
} from "@/lib/wording";

import { DeckScene, type SceneName } from "./deck-scene";

/**
 * The empty deck, said as its cause: no posting at all (the list of websites is the way in), a
 * profile without any skill (every fit is near 0, under the threshold), or postings under the
 * threshold: the deck is done for today, and a small celebration plays. With a filter on, the
 * threshold line stays, since the cause may be the filter. Each cause has the action that fixes
 * it; while the counts load, the threshold line shows. The Rive scene stands where EmptyState
 * puts its icon, and the icon stays when the scene cannot play.
 */
export function DeckEmpty({
  threshold,
  filtered,
  motion,
}: {
  threshold: number;
  filtered: boolean;
  motion: boolean;
}) {
  const postings = useJobCount({ enabled: !filtered });
  const profile = useProfile({ enabled: !filtered });
  const noPostings = !filtered && postings.data === 0;
  const noSkills =
    !filtered &&
    !noPostings &&
    profile.data !== undefined &&
    profile.data.sections.skills.have.every(
      (category) => category.terms.length === 0,
    );
  const [title, hint, actions, scene, Icon]: [
    string,
    string,
    ReactNode,
    SceneName,
    LucideIcon,
  ] = noPostings
    ? [
        WORDING.deckNoPostings,
        WORDING.deckNoPostingsHint,
        <Button key="find" asChild>
          <Link to="/jobs?find=1">{WORDING.findJobs}</Link>
        </Button>,
        "Empty",
        Layers,
      ]
    : noSkills
      ? [
          deckNoSkills(threshold),
          DECK_NO_SKILLS_HINT,
          <>
            <Button asChild>
              <Link to="/profile?tab=skills">{WORDING.addSkills}</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/settings/search">{WORDING.searchOptions}</Link>
            </Button>
          </>,
          "Empty",
          Layers,
        ]
      : [
          deckEmpty(threshold),
          WORDING.deckEmptyHint,
          <>
            <Button asChild>
              <Link to="/jobs?find=1">{WORDING.findNewPostings}</Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/settings/search">{WORDING.searchOptions}</Link>
            </Button>
          </>,
          "Done",
          PartyPopper,
        ];
  return (
    <section aria-label={WORDING.deck} className="w-full">
      <Empty className="gap-5 rounded-3xl bg-card/80 px-6 py-10 shadow-lg ring-1 ring-foreground/5 backdrop-blur-sm md:px-8 md:py-12">
        <EmptyHeader className="gap-2">
          <EmptyMedia className="mb-1">
            <DeckScene
              key={scene}
              scene={scene}
              motion={motion}
              className="h-40 w-56"
              fallback={
                <span className="flex size-12 items-center justify-center rounded-xl bg-muted text-foreground">
                  <Icon aria-hidden="true" className="size-6" />
                </span>
              }
            />
          </EmptyMedia>
          <EmptyTitle className="text-lg tracking-normal">{title}</EmptyTitle>
          <EmptyDescription>{hint}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="flex-row flex-wrap justify-center">
          {actions}
        </EmptyContent>
      </Empty>
    </section>
  );
}
