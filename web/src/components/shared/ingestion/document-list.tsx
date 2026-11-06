import {
  ClipboardList,
  Eye,
  FileArchive,
  FileImage,
  FileText,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Empty, EmptyDescription } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { absoluteTime, relativeTime } from "@/features/profile/helpers/when";
import { cn } from "@/lib/utils";

import {
  failureSentence,
  type IngestionDocument,
} from "@/features/ingestion/api/documents-api";
import { useConfirmDocument } from "@/features/ingestion/api/use-documents";
import {
  hasText,
  kindLabel,
  needsConfirmation,
  pagesLabel,
  sizeLabel,
  statusView,
  type StatusTone,
} from "@/features/ingestion/helpers/labels";

const TONES: Record<StatusTone, string> = {
  done: "border-success/30 bg-success/15 text-success",
  working: "border-border bg-muted text-foreground",
  waiting: "border-warning/40 bg-warning-muted text-foreground",
  failed: "border-destructive/40 bg-destructive/10 text-destructive",
};

/**
 * The user's documents, newest first: name, type, size, state, what was not read, and for each
 * the preview of its text and its deletion. A document whose text looks like it holds a secret
 * asks to be checked, kept or deleted.
 */
export function DocumentList({
  documents,
  onPreview,
  onDelete,
}: {
  documents: IngestionDocument[];
  onPreview: (document: IngestionDocument) => void;
  onDelete: (document: IngestionDocument) => void;
}) {
  if (documents.length === 0)
    return (
      <Empty className="border px-4 py-6 md:px-4 md:py-6">
        <EmptyDescription>
          No document yet. Add a file or paste a text above.
        </EmptyDescription>
      </Empty>
    );
  return (
    <ul aria-label="Your documents" className="flex flex-col gap-2">
      {documents.map((document) => (
        <DocumentRow
          key={document.id}
          document={document}
          onPreview={onPreview}
          onDelete={onDelete}
        />
      ))}
    </ul>
  );
}

function KindIcon({ document }: { document: IngestionDocument }) {
  const Icon =
    document.kind === "image"
      ? FileImage
      : document.kind === "archive"
        ? FileArchive
        : document.source === "pasted"
          ? ClipboardList
          : FileText;
  return (
    <Icon
      aria-hidden="true"
      className="mt-0.5 size-5 shrink-0 text-muted-foreground"
    />
  );
}

function DocumentRow({
  document,
  onPreview,
  onDelete,
}: {
  document: IngestionDocument;
  onPreview: (document: IngestionDocument) => void;
  onDelete: (document: IngestionDocument) => void;
}) {
  const status = statusView(document);
  const pages = pagesLabel(document);
  return (
    <li data-status={document.status}>
      <Card className="gap-2 px-4 py-3 sm:px-5 sm:py-4">
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <KindIcon document={document} />
          <div className="min-w-0 flex-1 basis-40">
            <p className="font-medium break-words">
              <bdi>{document.name}</bdi>
            </p>
            <p className="text-xs text-muted-foreground">
              {kindLabel(document)}, {sizeLabel(document.size)}, added{" "}
              <Tooltip>
                <TooltipTrigger asChild>
                  <time dateTime={document.created_at}>
                    {relativeTime(document.created_at)}
                  </time>
                </TooltipTrigger>
                <TooltipContent>
                  {absoluteTime(document.created_at)}
                </TooltipContent>
              </Tooltip>
            </p>
          </div>
          <Badge variant="outline" className={TONES[status.tone]}>
            {status.tone === "working" ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            {status.label}
          </Badge>
        </div>
        {status.detail ? (
          <p
            className={cn(
              "text-sm text-muted-foreground",
              status.tone === "failed" && "text-destructive",
            )}
          >
            {status.detail}
          </p>
        ) : null}
        {document.note ? (
          <p className="text-sm text-muted-foreground">{document.note}</p>
        ) : null}
        {pages ? (
          <p className="text-sm text-muted-foreground">{pages}</p>
        ) : null}
        {needsConfirmation(document) ? (
          <SecretWarning document={document} onDelete={onDelete} />
        ) : null}
        <div className="flex flex-wrap gap-2">
          {hasText(document) ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label={`Preview ${document.name}`}
              onClick={() => {
                onPreview(document);
              }}
            >
              <Eye aria-hidden="true" />
              Preview
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Delete ${document.name}`}
            onClick={() => {
              onDelete(document);
            }}
          >
            <Trash2 aria-hidden="true" />
            Delete
          </Button>
        </div>
      </Card>
    </li>
  );
}

/**
 * Shown on a document whose text matches the shape of a password or an access key. The user
 * looks (Preview), then keeps it or deletes it; until then no run reads the document.
 */
function SecretWarning({
  document,
  onDelete,
}: {
  document: IngestionDocument;
  onDelete: (document: IngestionDocument) => void;
}) {
  const confirm = useConfirmDocument(document.id);
  return (
    <div
      role="group"
      aria-label={`Possible secret in ${document.name}`}
      className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning-muted px-3 py-2.5 text-sm"
    >
      <p className="flex items-start gap-2 font-medium">
        <TriangleAlert
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-warning"
        />
        This may hold a password or an access key
      </p>
      <p className="text-muted-foreground">
        Look at the text first. No agent reads this document until you say it is
        fine. If a real password or key is in it, delete the document and change
        that password.
      </p>
      {confirm.isError ? (
        <p role="alert" className="text-destructive">
          {failureSentence(confirm.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={confirm.isPending}
          aria-busy={confirm.isPending}
          onClick={() => {
            confirm.mutate();
          }}
        >
          {confirm.isPending ? (
            <Spinner aria-hidden="true" role={undefined} />
          ) : null}
          It is fine, keep it
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            onDelete(document);
          }}
        >
          Delete it
        </Button>
      </div>
    </div>
  );
}
