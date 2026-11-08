import { Lock } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { PageBody } from "@/components/blocks/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { AddFiles } from "./add-files";
import { DeleteDocument } from "./delete-document";
import { DocumentList } from "./document-list";
import { DocumentPreview } from "./document-preview";
import { LoadError } from "@/components/blocks/load-error";
import { loadFailureSentence } from "@/features/ingestion/api/documents-api";
import {
  DEFAULT_LIMITS,
  megabytes,
  type DocumentList as Documents,
  type IngestionDocument,
  type StoredDocument,
} from "@/features/ingestion/api/documents-api";
import { isCvFile } from "@/features/documents/helpers/upload-cv";
import {
  useDocumentsCache,
  useWatchedDocuments,
} from "@/features/ingestion/api/use-documents";
import { sizeLabel } from "@/features/ingestion/helpers/labels";
import { PasteBox } from "./paste-box";
import { useUploads } from "@/features/ingestion/api/use-uploads";

/**
 * After an upload is stopped the list is asked again at once, and once more after this long:
 * when every byte had left before the stop, the server is still storing the file at the first
 * look.
 */
export const CANCEL_RECHECK_MS = 3000;

/**
 * Step 2, "Documents": add files or paste a text, then see what the app keeps: each document
 * with its type, size and state, the preview of its text, and its deletion. On its page the
 * ways to add sit on one card and the documents beside them; `flat` stacks everything without a
 * card of its own, for a place that is already one (the welcome wizard). With `onCv`, a PDF or
 * Word file added in this visit offers "This is my CV", which hands its file over; `cvId` is
 * the document already chosen. `autoCv` hands over the first one at once, while none is chosen.
 */
export function StepDocuments({
  flat = false,
  onCv,
  cvId,
  autoCv = false,
}: {
  flat?: boolean;
  onCv?: (file: File) => void;
  cvId?: string;
  autoCv?: boolean;
}) {
  const query = useWatchedDocuments();
  const cache = useDocumentsCache();
  const [previewing, setPreviewing] = useState<IngestionDocument>();
  const [deleting, setDeleting] = useState<IngestionDocument>();
  const listHeading = useRef<HTMLHeadingElement>(null);

  const onStored = cache.stored;
  // The files sent in this visit, by document: the CV conversion needs the file itself.
  const [sent, setSent] = useState<ReadonlyMap<string, File>>(new Map());
  // Read by the upload callback, which outlives a render.
  const latest = useRef({ autoCv, onCv, cvId, taken: false });
  useEffect(() => {
    latest.current = { ...latest.current, autoCv, onCv, cvId };
  }, [autoCv, onCv, cvId]);
  const onSent = useCallback(
    (stored: StoredDocument, file?: File) => {
      onStored(stored);
      if (!file || !isCvFile(file.name)) return;
      setSent((before) => new Map(before).set(stored.id, file));
      const now = latest.current;
      if (now.autoCv && now.cvId === undefined && !now.taken && now.onCv) {
        now.taken = true;
        now.onCv(file);
      }
    },
    [onStored],
  );
  const recheck = useRef<number>();
  const onCancelled = useCallback(() => {
    cache.refresh();
    window.clearTimeout(recheck.current);
    recheck.current = window.setTimeout(cache.refresh, CANCEL_RECHECK_MS);
  }, [cache]);
  useEffect(
    () => () => {
      window.clearTimeout(recheck.current);
    },
    [],
  );
  const uploads = useUploads(
    useMemo(() => ({ onStored: onSent, onCancelled }), [onSent, onCancelled]),
  );
  const limits = query.data?.limits ?? DEFAULT_LIMITS;

  const privacy = (
    <p className="flex max-w-prose items-start gap-2 text-sm text-muted-foreground">
      <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        Your documents stay on this server, and no other user of this app can
        see them. Nothing is sent to an AI model until you start a run, and the
        app asks you first.
      </span>
    </p>
  );
  const adding = (
    <>
      <AddFiles limits={limits} uploads={uploads} />
      <PasteBox limits={limits} onStored={onStored} />
    </>
  );
  const documents = (
    <section
      aria-labelledby="documents-heading"
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3
          id="documents-heading"
          ref={listHeading}
          tabIndex={-1}
          className="text-base font-medium tracking-tight outline-none"
        >
          Your documents
        </h3>
        {query.data ? <Used list={query.data} /> : null}
      </div>
      {query.isPending ? (
        <div role="status" aria-live="polite" className="flex flex-col gap-2">
          <span className="sr-only">Loading your documents</span>
          <Skeleton aria-hidden="true" className="h-20" />
          <Skeleton aria-hidden="true" className="h-20" />
        </div>
      ) : query.isError ? (
        <LoadError query={query} reason={loadFailureSentence(query.error)}>
          Your documents could not be loaded.
        </LoadError>
      ) : (
        <DocumentList
          documents={query.data.items}
          flat={flat}
          onPreview={setPreviewing}
          onDelete={setDeleting}
          action={
            onCv
              ? (document) => {
                  if (document.id === cvId)
                    return <Badge variant="secondary">Your CV</Badge>;
                  const file = sent.get(document.id);
                  return file ? (
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={`This is my CV: ${document.name}`}
                      onClick={() => {
                        onCv(file);
                      }}
                    >
                      This is my CV
                    </Button>
                  ) : null;
                }
              : undefined
          }
        />
      )}
    </section>
  );

  return (
    <>
      {flat ? (
        <div className="flex flex-col gap-6">
          {privacy}
          {adding}
          {documents}
        </div>
      ) : (
        <PageBody aside={documents}>
          {privacy}
          <Card>
            {/* The two ways to add side by side once the column is wide enough. */}
            <CardContent className="@container">
              <div className="grid items-start gap-6 @2xl:grid-cols-2">
                {adding}
              </div>
            </CardContent>
          </Card>
        </PageBody>
      )}

      <DocumentPreview
        document={previewing}
        onClose={() => {
          setPreviewing(undefined);
        }}
      />
      <DeleteDocument
        document={deleting}
        onClose={() => {
          setDeleting(undefined);
        }}
        onDeleted={(gone) => {
          setDeleting(undefined);
          cache.deleted(gone);
          toast.success("Document deleted.");
        }}
        focusAfterDelete={listHeading}
      />
    </>
  );
}

/** "3 documents, 4.2 MB of 100 MB used". */
function Used({ list }: { list: Documents }) {
  const count = list.usage.documents;
  if (count === 0) return null;
  return (
    <p className="text-xs text-muted-foreground tabular-nums">
      {count} {count === 1 ? "document" : "documents"},{" "}
      {sizeLabel(list.usage.bytes)} of {megabytes(list.limits.user_bytes)} MB
      used
    </p>
  );
}
