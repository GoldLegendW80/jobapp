import { useInfiniteQuery } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  documentTextKey,
  failureSentence,
  getDocumentText,
  type IngestionDocument,
} from "./documents-api";

const number = new Intl.NumberFormat("en");
/** The pages of a PDF are joined by a form feed in the stored text. */
const PAGE_BREAK = "\f";

/**
 * The text kept from a document, as an agent will read it, 20,000 characters at a time. Open
 * while `document` is set. Nothing of the text stays in the page's memory once it is closed.
 */
export function DocumentPreview({
  document,
  onClose,
}: {
  document: IngestionDocument | undefined;
  onClose: () => void;
}) {
  // The last document shown stays while the dialog closes.
  const [shown, setShown] = useState(document);
  if (document && document !== shown) setShown(document);
  return (
    <Dialog
      open={document !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        {shown ? <Preview key={shown.id} document={shown} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function Preview({ document }: { document: IngestionDocument }) {
  const query = useInfiniteQuery({
    queryKey: documentTextKey(document.id),
    queryFn: ({ pageParam, signal }) =>
      getDocumentText(document.id, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    // Dropped as soon as the dialog closes: a document's text is not kept around.
    gcTime: 0,
  });
  const pages = query.data?.pages ?? [];
  const text = pages.map((page) => page.text).join("");
  const total = pages[0]?.total ?? 0;
  const shown = pages.reduce((sum, page) => sum + page.text.length, 0);

  return (
    <>
      <DialogHeader>
        <DialogTitle className="pr-6 break-words">
          <bdi>{document.name}</bdi>
        </DialogTitle>
        <DialogDescription>
          The text kept from this document, as an agent will read it.
        </DialogDescription>
      </DialogHeader>
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the text
        </p>
      ) : query.isError && pages.length === 0 ? (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm text-destructive">
            The text could not be loaded. {failureSentence(query.error)}
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : (
        <>
          <div
            role="region"
            aria-label="Text of the document"
            tabIndex={0}
            className="max-h-[55vh] overflow-y-auto rounded-md border bg-muted/40 p-3 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <PreviewText text={text} waiting={document.status !== "done"} />
          </div>
          <DialogFooter className="items-center sm:justify-between">
            <p className="text-xs text-muted-foreground tabular-nums">
              {query.hasNextPage
                ? `The first ${number.format(shown)} of ${number.format(total)} characters`
                : `${number.format(total)} ${total === 1 ? "character" : "characters"}`}
            </p>
            {query.hasNextPage ? (
              <Button
                variant="outline"
                size="sm"
                disabled={query.isFetchingNextPage}
                aria-busy={query.isFetchingNextPage}
                onClick={() => void query.fetchNextPage()}
              >
                {query.isFetchingNextPage ? (
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                ) : null}
                Show more
              </Button>
            ) : null}
          </DialogFooter>
          {query.isFetchNextPageError ? (
            <p role="alert" className="text-sm text-destructive">
              The rest could not be loaded. {failureSentence(query.error)}
            </p>
          ) : null}
        </>
      )}
    </>
  );
}

const TEXT =
  "font-sans text-sm leading-relaxed break-words whitespace-pre-wrap";

function PreviewText({ text, waiting }: { text: string; waiting: boolean }) {
  if (text === "")
    return (
      <p className="text-sm text-muted-foreground">
        There is no text in this document.
      </p>
    );
  const parts = text.split(PAGE_BREAK);
  if (parts.length === 1) return <pre className={TEXT}>{text}</pre>;
  return (
    <ol className="flex flex-col gap-4">
      {parts.map((part, index) => (
        // Pages have no identity but their place in the document.
        <li key={index} className="flex flex-col gap-1">
          <p className="text-xs font-medium text-muted-foreground">
            Page {index + 1}
          </p>
          {part === "" ? (
            <p className="text-sm text-muted-foreground">
              {waiting
                ? "This page is an image. The agent will read it at the next run."
                : "This page holds no text."}
            </p>
          ) : (
            <pre className={TEXT}>{part}</pre>
          )}
        </li>
      ))}
    </ol>
  );
}
