import { ShieldCheck, Zap } from "lucide-react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { WORDING } from "@/lib/wording";
import { cn } from "@/lib/utils";

import type { Mode } from "@/features/opportunities/api/opportunities-api";
import { MODE_WORDS } from "@/features/opportunities/helpers/deck/card-text";

/**
 * Above the deck: the page's name, the postings left (said politely when it changes), and the
 * mode a Like runs in, always in sight before a Like, linked to where it is changed. Full auto
 * stands out in the brand colour: there a Like is the approval to send.
 */
export function DeckTopBar({
  mode,
  left,
}: {
  mode: Mode;
  /** The postings left, while a card is shown. */
  left: number | undefined;
}) {
  const Icon = mode === "full_auto" ? Zap : ShieldCheck;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          {WORDING.discover}
        </h1>
        <p
          aria-live="polite"
          className="text-sm text-muted-foreground tabular-nums"
        >
          {left === undefined
            ? ""
            : left === 1
              ? "1 posting left"
              : `${left} postings left`}
        </p>
      </div>
      <Badge
        variant={mode === "full_auto" ? "brand" : "outline"}
        asChild
        className={cn(
          "min-h-9 gap-1.5 px-3 text-sm shadow-sm [&>svg]:size-4",
          mode === "full_auto"
            ? "hover:bg-brand/90"
            : "bg-card text-foreground hover:bg-accent",
        )}
      >
        <Link
          to="/settings/applications"
          aria-label={`A Like runs in ${MODE_WORDS[mode]}. Change it in the Applications settings`}
          data-testid="deck-mode"
        >
          <Icon aria-hidden="true" />
          {`${WORDING.like}: ${MODE_WORDS[mode]}`}
        </Link>
      </Badge>
    </div>
  );
}
