import { ArrowLeft, FileText, RotateCw } from "lucide-react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { useJob } from "@/features/jobs/api/use-job";
import { NotFoundPage } from "@/components/blocks/not-found-page";
import { describeError, isApiError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { DocumentView } from "@/components/shared/documents/document-view";
import { usePostingDocuments } from "../api/use-posting-documents";
import { KINDS } from "../api/documents-api";
import { documentOf } from "../helpers/posting-documents-state";

const NAMES = { cv: "CV", letter: "Letter" } as const;

/** /jobs/:id/documents/:kind: the document view for the posting's CV or letter. */
export function PostingDocumentPage() {
  const { id = "", kind = "" } = useParams();
  const known = KINDS.find((one) => one === kind);
  const job = useJob(id);
  const documents = usePostingDocuments(id);
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
      <PageHeader
        title={NAMES[known]}
        description={
          job.data ? (
            <span className="break-words">
              {`${job.data.title} at ${job.data.company}`}
            </span>
          ) : undefined
        }
      />
      {documents.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
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
        <Empty className="rounded-xl bg-card">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileText aria-hidden="true" />
            </EmptyMedia>
            <EmptyTitle>
              The {known === "cv" ? "CV" : "letter"} of this posting has not
              been written yet.
            </EmptyTitle>
            <EmptyDescription>Write it from the posting page.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <DocumentView
          documentId={document.id}
          generation={document.generation}
        />
      )}
    </div>
  );
}
