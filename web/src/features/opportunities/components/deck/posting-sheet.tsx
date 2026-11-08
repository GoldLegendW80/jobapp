import { ExternalLink } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Spinner } from "@/components/ui/spinner";
import { PostingNotices } from "@/components/shared/jobs/posting/posting-notices";
import { useJob } from "@/features/jobs/api/use-job";
import { paragraphs, safeHttpUrl } from "@/features/jobs/helpers/text";
import { useIsPhone } from "@/hooks/use-media-query";
import { WORDING } from "@/lib/wording";
import { describeError } from "@/lib/api";

import type { Card } from "@/features/opportunities/api/opportunities-api";
import {
  contractLine,
  placeLine,
  sourceLine,
} from "@/features/opportunities/helpers/deck/card-text";

/** The posting's text, read when the panel opens. */
function PostingText({ card, open }: { card: Card; open: boolean }) {
  const job = useJob(card.id, { enabled: open });
  const text = job.data?.posting_text?.trim();
  if (job.isPending)
    return (
      <p className="flex items-center gap-2 text-muted-foreground">
        <Spinner aria-hidden="true" role={undefined} />
        Loading the posting
      </p>
    );
  if (job.isError) return <p role="alert">{describeError(job.error)}</p>;
  if (!text)
    return (
      <p className="text-muted-foreground">
        No posting text was saved for this posting.
      </p>
    );
  return (
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
  );
}

/** The facts of the card in one line, under the company. */
function factsLine(card: Card): string {
  return [placeLine(card), contractLine(card), card.pay ?? WORDING.payNotGiven]
    .filter(Boolean)
    .join(", ");
}

/** The posting's page in the app, and the posting at its source when it has one. */
function Links({ card }: { card: Card }) {
  const url = safeHttpUrl(card.url);
  const source = sourceLine(card.source);
  return (
    <>
      <Button asChild>
        <Link to={`/jobs/${encodeURIComponent(card.id)}`}>
          {WORDING.openPostingPage}
        </Link>
      </Button>
      {url && source ? (
        <Button asChild variant="outline">
          <a href={url} target="_blank" rel="noreferrer noopener">
            {source}
            <ExternalLink aria-hidden="true" />
            <span className="sr-only">{WORDING.opensNewTab}</span>
          </a>
        </Button>
      ) : null}
    </>
  );
}

const BODY =
  "min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-2 text-sm leading-relaxed sm:px-6";

/**
 * "More": the whole posting, read when it opens. On a phone a drawer rises from the bottom (dragged
 * down by its handle to close); wider, a sheet slides in from the right beside the deck. The title
 * and company head it, the text scrolls, the links stay at the foot.
 */
export function PostingSheet({
  card,
  open,
  onOpenChange,
}: {
  card: Card;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const phone = useIsPhone();
  if (phone)
    return (
      <Drawer open={open} onOpenChange={onOpenChange}>
        <DrawerContent className="max-h-[92dvh]">
          <DrawerHeader className="text-left">
            <DrawerTitle className="text-lg leading-tight">
              {card.title}
            </DrawerTitle>
            <DrawerDescription>
              {card.company}. {factsLine(card)}
            </DrawerDescription>
          </DrawerHeader>
          <div className={BODY}>
            <PostingNotices notices={card.notices} className="mb-4" />
            <PostingText card={card} open={open} />
          </div>
          <DrawerFooter>
            <Links card={card} />
          </DrawerFooter>
        </DrawerContent>
      </Drawer>
    );
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0 sm:max-w-xl">
        <SheetHeader className="pr-12">
          <SheetTitle className="text-lg leading-tight">
            {card.title}
          </SheetTitle>
          <SheetDescription>
            {card.company}. {factsLine(card)}
          </SheetDescription>
        </SheetHeader>
        <div className={BODY}>
          <PostingNotices notices={card.notices} className="mb-4" />
          <PostingText card={card} open={open} />
        </div>
        <SheetFooter className="flex-row flex-wrap">
          <Links card={card} />
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
