import { FileText } from "lucide-react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { NotFoundPage } from "@/components/blocks/not-found-page";
import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import { useJob } from "@/features/jobs/api/use-job";
import { isApiError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { DocumentView } from "@/components/shared/documents/document-view";
import { usePostingDocuments } from "../api/use-posting-documents";
import { KINDS } from "../api/documents-api";
import { documentOf } from "../helpers/posting-documents-state";

const NAMES = { cv: "CV", letter: "Letter" } as const;

/**
 * /jobs/:id/documents/:kind: the posting's CV or letter. Its header names the document and the
 * posting and holds the PDF; the page below it, the views, the facts and the versions beside.
 */
export function PostingDocumentPage() {
  const { id = "", kind = "" } = useParams();
  const known = KINDS.find((one) => one === kind);
  const job = useJob(id);
  const documents = usePostingDocuments(id);
  const title =
    known && job.data
      ? `${NAMES[known]} for ${job.data.title} at ${job.data.company}`
      : known
        ? NAMES[known]
        : "Document";
  useTitle(title);

  if (known === undefined) return <NotFoundPage />;
  if (
    (job.isError && isApiError(job.error) && job.error.status === 404) ||
    (documents.isError &&
      isApiError(documents.error) &&
      documents.error.status === 404)
  )
    return <NotFoundPage />;

  const trail = [
    { to: "/jobs", label: "Jobs" },
    { to: `/jobs/${id}`, label: job.data?.title ?? "Posting" },
  ];
  const document = documentOf(documents.data, known);
  if (document !== undefined)
    return (
      <Page>
        <DocumentView
          documentId={document.id}
          generation={document.generation}
          header={{ trail, title }}
        />
      </Page>
    );
  return (
    <Page>
      <PageHeader trail={trail} title={title} />
      {documents.isPending ? (
        <LoadingCard>Loading the document</LoadingCard>
      ) : documents.isError ? (
        <LoadError query={documents}>
          The document could not be loaded.
        </LoadError>
      ) : (
        <EmptyState
          icon={FileText}
          title={`The ${known === "cv" ? "CV" : "letter"} of this posting has not been written yet`}
          description="Write it from the posting page."
          actions={
            <Button asChild variant="outline">
              <Link to={`/jobs/${id}`}>Open the posting</Link>
            </Button>
          }
        />
      )}
    </Page>
  );
}
