import { FileText } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { Citation } from "../../api/proposals-api";
import { cut } from "@/features/ingestion/helpers/proposal-words";

/** The places in the documents a value comes from, each quote opening its document there. */
export function CitationList({
  citations,
  label,
  onOpen,
}: {
  citations: Citation[];
  label: string;
  onOpen: (citation: Citation) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <ul aria-label={label} className="flex flex-col gap-1">
      {citations.map((citation) => (
        <CitationLine
          key={`${citation.document_id}:${String(citation.start)}:${String(citation.end)}`}
          citation={citation}
          onOpen={onOpen}
        />
      ))}
    </ul>
  );
}

function CitationLine({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen: (citation: Citation) => void;
}) {
  const quote = (
    <q className="break-words">
      <mark>{cut(citation.excerpt)}</mark>
    </q>
  );
  if (citation.document === null || citation.source_deleted)
    return (
      <li className="text-sm">
        <span className="font-medium text-muted-foreground">
          Source deleted
        </span>
        {citation.source_deleted ? null : <> {quote}</>}
      </li>
    );
  return (
    <li className="text-sm">
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0 align-baseline"
        onClick={() => {
          onOpen(citation);
        }}
      >
        <FileText aria-hidden="true" />
        <bdi>{citation.document.name ?? "Document"}</bdi>
        <span className="sr-only">, open at the quote</span>
      </Button>{" "}
      {quote}
      {citation.transcribed ? (
        <span className="text-muted-foreground"> (transcribed, check it)</span>
      ) : null}
    </li>
  );
}
