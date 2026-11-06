import { Check } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";

import type { Citation, Review } from "../../api/proposals-api";
import { useAcceptAdditions } from "@/features/ingestion/api/use-accept-additions";
import {
  ADDITIONS_NOTE,
  PAGE,
} from "@/features/ingestion/helpers/proposal-words";
import type { ProposalContext } from "./proposal-card";
import { ProfileCompleteness } from "./profile-completeness";
import { QuotePreview } from "./quote-preview";
import { ReviewSection } from "./review-section";
import { ReviewSummary } from "./review-summary";

/** The review once read: its summary, "Accept all additions", each section, the quote dialog. */
export function LoadedReview({ review }: { review: Review }) {
  const [opened, setOpened] = useState<Citation>();
  // The last citation shown stays while the dialog closes.
  const [shown, setShown] = useState<Citation>();
  const [said, setSaid] = useState("");
  if (opened && opened !== shown) setShown(opened);
  // How many proposals each section shows: the bulk decisions take only what is on the screen.
  const [listed, setListed] = useState<Readonly<Record<string, number>>>({});
  const additions = review.sections.flatMap((section) =>
    section.items
      .slice(0, listed[section.section] ?? PAGE)
      .filter((item) => item.status === "pending" && item.bulk === null),
  );
  const { all, outcome } = useAcceptAdditions(review.run_id, additions);
  const context: ProposalContext = {
    runId: review.run_id,
    revision: review.profile_revision,
    onOpen: setOpened,
    onSaid: setSaid,
    sectionBusy: all.isPending,
  };
  return (
    <div className="flex flex-col gap-4">
      <ReviewSummary review={review} />
      <ProfileCompleteness />
      {additions.length > 0 ? (
        <div className="flex flex-col gap-1">
          <Button
            className="w-full sm:w-fit"
            disabled={all.isPending}
            onClick={() => {
              all.mutate();
            }}
          >
            {all.isPending ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : (
              <Check aria-hidden="true" />
            )}
            Accept all additions ({additions.length})
          </Button>
          <p className="text-sm text-muted-foreground">{ADDITIONS_NOTE}</p>
        </div>
      ) : null}
      {/* Always in the page, so a screen reader hears the outcome when it arrives. */}
      <p role="status" className="text-sm font-medium empty:hidden">
        {outcome}
      </p>
      <p role="status" className="sr-only">
        {said}
      </p>
      {review.sections.map((section) => (
        <ReviewSection
          key={section.section}
          section={section}
          context={context}
          shown={listed[section.section] ?? PAGE}
          onShowMore={() => {
            setListed((old) => ({
              ...old,
              [section.section]: (old[section.section] ?? PAGE) + PAGE,
            }));
          }}
        />
      ))}
      <Dialog
        open={opened !== undefined}
        onOpenChange={(open) => {
          if (!open) setOpened(undefined);
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          {shown ? (
            <QuotePreview
              key={`${shown.document_id}:${String(shown.start)}`}
              citation={shown}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
