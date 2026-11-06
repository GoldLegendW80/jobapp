import { Check, X } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";

import type { ProposalSection } from "../../api/proposals-api";
import { useSectionDecision } from "@/features/ingestion/api/use-section-decision";
import { sectionShown } from "@/features/ingestion/helpers/proposal-outcomes";
import {
  PAGE,
  plural,
  sectionName,
} from "@/features/ingestion/helpers/proposal-words";
import { ProposalCard, type ProposalContext } from "./proposal-card";

/**
 * One section of the profile and its proposals, with "Accept all in this section" and "Reject the
 * rest of this section". The two bulk decisions are about what is on the screen, never about a
 * proposal the user has not been shown yet.
 */
export function ReviewSection({
  section,
  context,
  shown,
  onShowMore,
}: {
  section: ProposalSection;
  context: ProposalContext;
  /** How many of its proposals the page shows: the bulk decisions take those only. */
  shown: number;
  onShowMore: () => void;
}) {
  const name = sectionName(section.section);
  const id = `proposals-${section.section}`;
  const heading = useRef<HTMLHeadingElement>(null);
  const onScreen = sectionShown(section, shown);
  const { listed, pending, acceptable, behind } = onScreen;
  const { decide, outcome, problem, stopped } = useSectionDecision({
    runId: context.runId,
    section,
    shown: onScreen,
    heading,
  });
  const takes = acceptable > 0;
  // What the section holds beyond the page: the two buttons do not decide it, and say so.
  const accepting =
    behind > 0
      ? `Accept the ${String(acceptable)} shown`
      : "Accept all in this section";
  const rejecting =
    behind > 0
      ? `Reject the ${String(pending.length)} shown`
      : "Reject the rest of this section";
  const busy = decide.isPending || context.sectionBusy;
  const inner: ProposalContext = { ...context, sectionBusy: busy, stopped };
  const more = Math.min(PAGE, section.items.length - shown);
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <h4
          id={id}
          ref={heading}
          tabIndex={-1}
          className="font-medium outline-none focus-visible:underline"
        >
          {name}
        </h4>
        <p className="text-sm text-muted-foreground tabular-nums">
          {plural(section.counts.total, "proposal", "proposals")}
          {section.counts.pending < section.counts.total
            ? `, ${String(section.counts.pending)} left to review`
            : ""}
        </p>
      </div>
      {pending.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {takes ? (
            <Button
              size="sm"
              aria-label={`${accepting}: ${name}`}
              disabled={busy}
              onClick={() => {
                decide.mutate("accept");
              }}
            >
              <Check aria-hidden="true" />
              {accepting}
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            aria-label={`${rejecting}: ${name}`}
            disabled={busy}
            onClick={() => {
              decide.mutate("reject");
            }}
          >
            <X aria-hidden="true" />
            {rejecting}
          </Button>
          {takes ? null : (
            <p className="text-sm text-muted-foreground">
              What is left here is accepted one by one.
            </p>
          )}
        </div>
      ) : null}
      {/* Always in the page, so a screen reader hears the outcome when it arrives. */}
      <p role="status" className="text-sm empty:hidden">
        {outcome}
      </p>
      {problem ? (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {listed.map((item) => (
          <ProposalCard key={item.id} item={item} context={inner} />
        ))}
      </ul>
      {section.items.length > shown ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-muted-foreground tabular-nums">
            Showing {shown} of {section.items.length}.
          </p>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Show ${String(more)} more: ${name}`}
            onClick={onShowMore}
          >
            Show {more} more
          </Button>
        </div>
      ) : null}
    </section>
  );
}
