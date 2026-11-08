import {
  ClipboardList,
  Eye,
  FileArchive,
  FileImage,
  FileText,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/blocks/empty-state";
import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

/** How a document's chip shows its state: read, being read, waiting or failed. */
const ATTACHMENT_STATE = {
  done: "done",
  working: "processing",
  waiting: "idle",
  failed: "error",
} as const satisfies Record<StatusTone, string>;

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
  flat = false,
  onPreview,
  onDelete,
  action,
}: {
  documents: IngestionDocument[];
  /** Inside a card already: the empty state draws no box of its own. */
  flat?: boolean;
  onPreview: (document: IngestionDocument) => void;
  onDelete: (document: IngestionDocument) => void;
  /** A page's own action on a document, before Preview and Delete. */
  action?: (document: IngestionDocument) => ReactNode;
}) {
  if (documents.length === 0)
    return (
      <EmptyState
        icon={FileText}
        title="No document yet"
        description="Add a file or paste a text."
        flat={flat}
      />
    );
  return (
    <ul aria-label="Your documents" className="flex flex-col gap-2">
      {documents.map((document) => (
        <DocumentRow
          key={document.id}
          document={document}
          onPreview={onPreview}
          onDelete={onDelete}
          action={action?.(document)}
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
  return <Icon aria-hidden="true" />;
}

function DocumentRow({
  document,
  onPreview,
  onDelete,
  action,
}: {
  document: IngestionDocument;
  onPreview: (document: IngestionDocument) => void;
  onDelete: (document: IngestionDocument) => void;
  action?: ReactNode;
}) {
  const status = statusView(document);
  const pages = pagesLabel(document);
  // Under the file's line: why it waits or failed, what was not read, a possible secret.
  const notes = [status.detail, document.note, pages].filter(Boolean);
  return (
    <li data-status={document.status}>
      <Attachment state={ATTACHMENT_STATE[status.tone]} className="w-full">
        <AttachmentMedia>
          <KindIcon document={document} />
        </AttachmentMedia>
        {/* basis-40: on a narrow screen the state and the actions go under the name. */}
        <AttachmentContent className="basis-40">
          <AttachmentTitle>
            <bdi>{document.name}</bdi>
          </AttachmentTitle>
          <AttachmentDescription>
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
          </AttachmentDescription>
        </AttachmentContent>
        <AttachmentActions className="ml-auto gap-1">
          <Badge variant="outline" className={cn("mr-1", TONES[status.tone])}>
            {status.tone === "working" ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            {status.label}
          </Badge>
          {action}
          {hasText(document) ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <AttachmentAction
                  size="icon-sm"
                  aria-label={`Preview ${document.name}`}
                  onClick={() => {
                    onPreview(document);
                  }}
                >
                  <Eye aria-hidden="true" />
                </AttachmentAction>
              </TooltipTrigger>
              <TooltipContent>Preview</TooltipContent>
            </Tooltip>
          ) : null}
          <Tooltip>
            <TooltipTrigger asChild>
              <AttachmentAction
                size="icon-sm"
                aria-label={`Delete ${document.name}`}
                onClick={() => {
                  onDelete(document);
                }}
              >
                <Trash2 aria-hidden="true" />
              </AttachmentAction>
            </TooltipTrigger>
            <TooltipContent>Delete</TooltipContent>
          </Tooltip>
        </AttachmentActions>
        {notes.length > 0 || needsConfirmation(document) ? (
          <div className="flex basis-full flex-col gap-2 text-sm text-muted-foreground sm:pl-12">
            {status.detail ? (
              <p className={cn(status.tone === "failed" && "text-destructive")}>
                {status.detail}
              </p>
            ) : null}
            {document.note ? <p>{document.note}</p> : null}
            {pages ? <p>{pages}</p> : null}
            {needsConfirmation(document) ? (
              <SecretWarning document={document} onDelete={onDelete} />
            ) : null}
          </div>
        ) : null}
      </Attachment>
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
          variant="outline"
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
