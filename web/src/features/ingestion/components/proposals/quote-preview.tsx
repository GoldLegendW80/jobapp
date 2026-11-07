import {
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";

import { failureSentence } from "../../api/documents-api";
import { useQuoteText } from "../../api/use-documents";
import type { Citation } from "../../api/proposals-api";

/** Characters of the document shown before the quote when a citation is opened. */
const CONTEXT_BEFORE = 400;

/** The text of a document around one quote, the quote marked and scrolled into view. */
export function QuotePreview({ citation }: { citation: Citation }) {
  const from = Math.max(0, citation.start - CONTEXT_BEFORE);
  const query = useQuoteText(citation.document_id, from);
  // Offsets count characters as the api does, not UTF-16 units.
  const points = Array.from(query.data?.text ?? "");
  const offset = query.data?.offset ?? from;
  const start = citation.start - offset;
  const end = citation.end - offset;
  const quoted = points.slice(Math.max(0, start), Math.max(0, end)).join("");
  const found = start >= 0 && quoted !== "" && end <= points.length;
  return (
    <>
      <DialogHeader>
        <DialogTitle className="pr-6 break-words">
          <bdi>{citation.document?.name ?? "Document"}</bdi>
        </DialogTitle>
        <DialogDescription>
          The text kept from this document, at the place of the quote.
        </DialogDescription>
      </DialogHeader>
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
          Loading the text
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          The text could not be loaded. {failureSentence(query.error)}
        </p>
      ) : (
        <div
          role="region"
          aria-label="Text of the document"
          tabIndex={0}
          className="max-h-[55vh] overflow-y-auto rounded-md border bg-muted/40 p-3"
        >
          {found ? (
            <pre className="font-sans text-sm leading-relaxed break-words whitespace-pre-wrap">
              {points.slice(0, start).join("")}
              <mark
                data-testid="cited-quote"
                ref={(node) => {
                  if (node && typeof node.scrollIntoView === "function")
                    node.scrollIntoView({ block: "center" });
                }}
              >
                {quoted}
              </mark>
              {points.slice(end).join("")}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              The text of this document changed since it was read, so the quote
              cannot be shown in its place.
            </p>
          )}
        </div>
      )}
    </>
  );
}
