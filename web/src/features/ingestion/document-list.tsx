import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ClipboardList,
  Eye,
  FileArchive,
  FileImage,
  FileText,
  LoaderCircle,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { absoluteTime, relativeTime } from "@/features/profile/when";
import { cn } from "@/lib/utils";

import {
  confirmDocument,
  documentsKey,
  failureSentence,
  type DocumentList as Documents,
  type IngestionDocument,
} from "./documents-api";
import {
  hasText,
  kindLabel,
  needsConfirmation,
  pagesLabel,
  sizeLabel,
  statusView,
  type StatusTone,
} from "./labels";

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
      <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
        No document yet. Add a file or paste a text above.
      </p>
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
    <li
      data-status={document.status}
      className="flex flex-col gap-2 rounded-lg border bg-card px-4 py-3"
    >
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <KindIcon document={document} />
        <div className="min-w-0 flex-1 basis-40">
          <p className="font-medium break-words">{document.name}</p>
          <p className="text-xs text-muted-foreground">
            {kindLabel(document)}, {sizeLabel(document.size)}, added{" "}
            <time
              dateTime={document.created_at}
              title={absoluteTime(document.created_at)}
            >
              {relativeTime(document.created_at)}
            </time>
          </p>
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
            TONES[status.tone],
          )}
        >
          {status.tone === "working" ? (
            <LoaderCircle aria-hidden="true" className="size-3 animate-spin" />
          ) : null}
          {status.label}
        </span>
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
      {pages ? <p className="text-sm text-muted-foreground">{pages}</p> : null}
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
  const client = useQueryClient();
  const confirm = useMutation({
    mutationFn: () => confirmDocument(document.id),
    onSuccess: (confirmed) => {
      client.setQueryData<Documents>(documentsKey, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === confirmed.id ? confirmed : item,
              ),
            }
          : old,
      );
    },
  });
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
            <LoaderCircle aria-hidden="true" className="animate-spin" />
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
