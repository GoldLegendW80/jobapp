import { Lock, RotateCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { AddFiles } from "./add-files";
import { DeleteDocument } from "./delete-document";
import { DocumentList } from "./document-list";
import { DocumentPreview } from "./document-preview";
import {
  DEFAULT_LIMITS,
  loadFailureSentence,
  megabytes,
  type DocumentList as Documents,
  type IngestionDocument,
} from "@/features/ingestion/api/documents-api";
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
 * with its type, size and state, the preview of its text, and its deletion.
 */
export function StepDocuments() {
  const query = useWatchedDocuments();
  const cache = useDocumentsCache();
  const [previewing, setPreviewing] = useState<IngestionDocument>();
  const [deleting, setDeleting] = useState<IngestionDocument>();
  const listHeading = useRef<HTMLHeadingElement>(null);

  const onStored = cache.stored;
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
    useMemo(() => ({ onStored, onCancelled }), [onStored, onCancelled]),
  );
  const limits = query.data?.limits ?? DEFAULT_LIMITS;

  return (
    <div className="flex flex-col gap-6">
      <p className="flex items-start gap-2 text-sm text-muted-foreground">
        <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span>
          Your documents stay on this server, and no other user of this app can
          see them. Nothing is sent to an AI model until you start a run, and
          the app asks you first.
        </span>
      </p>
      <AddFiles limits={limits} uploads={uploads} />
      <PasteBox limits={limits} onStored={onStored} />

      <section
        aria-labelledby="documents-heading"
        className="flex flex-col gap-3"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3
            id="documents-heading"
            ref={listHeading}
            tabIndex={-1}
            className="text-base font-semibold tracking-tight outline-none"
          >
            Your documents
          </h3>
          {query.data ? <Used list={query.data} /> : null}
        </div>
        {query.isPending ? (
          <p
            role="status"
            className="flex items-center gap-2 text-sm text-muted-foreground"
          >
            <Spinner aria-hidden="true" role={undefined} />
            Loading your documents
          </p>
        ) : query.isError ? (
          <div className="flex flex-col items-start gap-3">
            <Alert variant="destructive">
              <AlertTitle>Your documents could not be loaded</AlertTitle>
              <AlertDescription>
                {loadFailureSentence(query.error)}
              </AlertDescription>
            </Alert>
            <Button
              variant="outline"
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </div>
        ) : (
          <DocumentList
            documents={query.data.items}
            onPreview={setPreviewing}
            onDelete={setDeleting}
          />
        )}
      </section>

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
    </div>
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
