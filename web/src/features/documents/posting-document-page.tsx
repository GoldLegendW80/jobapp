import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, LoaderCircle, RotateCw } from "lucide-react";
import { Link, useParams } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getJob, jobKey } from "@/features/jobs/api";
import { NotFoundPage } from "@/features/shell/not-found-page";
import { describeError, isApiError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { DocumentView } from "./document-view";
import {
  getPostingDocuments,
  KINDS,
  postingDocumentsKey,
} from "./documents-api";
import { documentOf, postingPollInterval } from "./posting-documents-state";

const NAMES = { cv: "CV", letter: "Letter" } as const;

/** /jobs/:id/documents/:kind: the document view for the posting's CV or letter. */
export function PostingDocumentPage() {
  const { id = "", kind = "" } = useParams();
  const known = KINDS.find((one) => one === kind);
  const job = useQuery({
    queryKey: jobKey(id),
    queryFn: ({ signal }) => getJob(id, signal),
  });
  const documents = useQuery({
    queryKey: postingDocumentsKey(id),
    queryFn: ({ signal }) => getPostingDocuments(id, signal),
    refetchInterval: (current) => postingPollInterval(current.state.data),
  });
  useTitle(
    known && job.data
      ? `${NAMES[known]} for ${job.data.title} at ${job.data.company}`
      : "Document",
  );

  if (known === undefined) return <NotFoundPage />;
  if (
    (job.isError && isApiError(job.error) && job.error.status === 404) ||
    (documents.isError &&
      isApiError(documents.error) &&
      documents.error.status === 404)
  )
    return <NotFoundPage />;

  const document = documentOf(documents.data, known);
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <Button asChild variant="ghost" size="sm" className="w-fit">
        <Link to={`/jobs/${id}`}>
          <ArrowLeft aria-hidden="true" />
          Back to the posting
        </Link>
      </Button>
      <header className="flex flex-col gap-1">
        <p className="text-sm font-medium text-muted-foreground break-words">
          {job.data ? `${job.data.title} at ${job.data.company}` : ""}
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">
          {NAMES[known]}
        </h1>
      </header>
      {documents.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the document
        </p>
      ) : documents.isError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-col gap-3">
            <p>
              The document could not be loaded. {describeError(documents.error)}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => void documents.refetch()}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : document === undefined ? (
        <p className="text-sm text-muted-foreground">
          The {known === "cv" ? "CV" : "letter"} of this posting has not been
          written yet. Write it from the posting page.
        </p>
      ) : (
        <DocumentView
          documentId={document.id}
          generation={document.generation}
        />
      )}
    </div>
  );
}
