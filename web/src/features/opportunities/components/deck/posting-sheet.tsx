import { Link } from "react-router";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import { useJob } from "@/features/jobs/api/use-job";
import { paragraphs } from "@/features/jobs/helpers/text";
import { WORDING } from "@/lib/wording";
import { describeError } from "@/lib/api";

import type { Card } from "@/features/opportunities/api/opportunities-api";

/** "More": the posting's text in a sheet from the bottom, read when it opens. */
export function PostingSheet({
  card,
  open,
  onOpenChange,
}: {
  card: Card;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const job = useJob(card.id, { enabled: open });
  const text = job.data?.posting_text?.trim();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="max-h-[85dvh] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{card.title}</SheetTitle>
          <SheetDescription>{card.company}</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-3 px-4 pb-6 text-sm leading-relaxed">
          {job.isPending ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Spinner aria-hidden="true" role={undefined} />
              Loading the posting
            </p>
          ) : job.isError ? (
            <p role="alert">{describeError(job.error)}</p>
          ) : text ? (
            <div
              data-testid="deck-posting-text"
              className="flex max-w-prose flex-col gap-3"
            >
              {paragraphs(text).map((paragraph, index) => (
                <p key={index} className="break-words whitespace-pre-line">
                  {paragraph}
                </p>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">
              No posting text was saved for this posting.
            </p>
          )}
          <Link
            to={`/jobs/${encodeURIComponent(card.id)}`}
            className="w-fit text-primary underline-offset-4 hover:underline"
          >
            {WORDING.openPostingPage}
          </Link>
        </div>
      </SheetContent>
    </Sheet>
  );
}
