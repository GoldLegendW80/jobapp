import { ArrowLeft, ArrowRight } from "lucide-react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Kbd } from "@/components/ui/kbd";
import { WORDING } from "@/lib/wording";

import type {
  DeckFilters,
  Mode,
} from "@/features/opportunities/api/opportunities-api";
import {
  MODE_SENTENCES,
  MODE_WORDS,
} from "@/features/opportunities/helpers/deck/card-text";
import { DeckFilterRow } from "./filter-row";

/**
 * Beside the deck on a wide screen, under it on a phone: what a Like does in the mode in force,
 * with the way to change it, then the deck's keys (to a precise pointer only) and its filters
 * under Advanced.
 */
export function DeckRail({
  mode,
  keys,
  filters,
  sources,
  onFiltersChange,
}: {
  mode: Mode;
  keys: boolean;
  filters: DeckFilters;
  sources: readonly string[];
  onFiltersChange: (filters: DeckFilters) => void;
}) {
  return (
    <>
      <Section title={WORDING.afterLike}>
        <Card>
          <CardContent className="flex flex-col items-start gap-3">
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium">{MODE_WORDS[mode]}</p>
              <p className="text-sm text-muted-foreground">
                {MODE_SENTENCES[mode]}
              </p>
            </div>
            <Button asChild variant="outline" size="sm">
              <Link to="/settings/applications">{WORDING.changeTheMode}</Link>
            </Button>
          </CardContent>
        </Card>
      </Section>
      <Section title={WORDING.yourDeck}>
        <Card>
          <CardContent className="flex flex-col gap-4">
            {keys ? (
              <p
                data-testid="deck-keys"
                className="text-sm leading-7 text-muted-foreground"
              >
                Keys:{" "}
                <Kbd>
                  <ArrowLeft aria-hidden="true" />
                  <span className="sr-only">left arrow</span>
                </Kbd>{" "}
                to pass,{" "}
                <Kbd>
                  <ArrowRight aria-hidden="true" />
                  <span className="sr-only">right arrow</span>
                </Kbd>{" "}
                to like, <Kbd>U</Kbd> to undo the last one.
              </p>
            ) : null}
            <AdvancedSection pageKey="discover">
              <DeckFilterRow
                filters={filters}
                sources={sources}
                onChange={onFiltersChange}
              />
            </AdvancedSection>
          </CardContent>
        </Card>
      </Section>
    </>
  );
}
