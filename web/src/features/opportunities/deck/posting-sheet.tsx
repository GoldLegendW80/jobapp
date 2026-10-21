import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { Link } from "react-router";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { getJob, jobKey } from "@/features/jobs/api";
import { paragraphs } from "@/features/jobs/text";
import { describeError } from "@/lib/api";

import type { Card } from "../opportunities-api";

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
  const job = useQuery({
    queryKey: jobKey(card.id),
    queryFn: ({ signal }) => getJob(card.id, signal),
    enabled: open,
  });
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
              <LoaderCircle
                aria-hidden="true"
                className="size-4 animate-spin"
              />
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
            Open the posting page
          </Link>
        </div>
      </SheetContent>
    </Sheet>
  );
}
