import { useEffect, useState } from "react";
import { Link } from "react-router";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useProfile } from "@/features/profile/api/use-profile";
import { type JobDetail } from "@/features/jobs/api/jobs-api";

import { useBaseCvList } from "@/features/documents/api/use-base-cvs";
import {
  usePostingDocuments,
  useRefreshWhenWorkEnds,
  useWriteNow,
} from "@/features/documents/api/use-posting-documents";
import { useCurrentSpend } from "@/features/documents/api/use-spend";
import { failureSentence } from "@/features/documents/api/documents-api";
import { isOpen } from "@/features/documents/helpers/labels";
import { PostingDocumentRow } from "./posting-document-row";
import {
  cardState,
  documentOf,
  isBusy,
  LETTER_FOLLOW_MS,
} from "@/features/documents/helpers/posting-documents-state";

/**
 * The "Documents" section of the job page (SPEC-documents.md, "Job detail page"): the posting's
 * CV and letter as two rows of one card, and Write both now while neither is written. The
 * documents are read again while a generation is open or a page is being drawn, and for a short
 * while after the CV's generation closes, until the letter that follows it has its own. The
 * posting is read again when the work ends: the render that ends it may have moved the posting
 * to `cv_ready`.
 */
export function PostingDocumentsSection({ job }: { job: JobDetail }) {
  const [choice, setChoice] = useState<string>();
  // CV generations seen closing; 0 when no letter is expected or its generation is read.
  const [letterWindow, setLetterWindow] = useState(0);
  const documents = usePostingDocuments(job.id, letterWindow > 0);
  const spend = useCurrentSpend();
  const cvs = useBaseCvList();
  const noBase = cvs.data !== undefined && cvs.data.base_cvs.length === 0;
  const nothingWritten =
    documents.data !== undefined && documents.data.documents.length === 0;
  const profile = useProfile({ enabled: noBase && nothingWritten });
  const noExperience =
    noBase &&
    nothingWritten &&
    profile.data !== undefined &&
    profile.data.sections.experience.employers.length === 0;

  useRefreshWhenWorkEnds(
    job.id,
    (documents.data?.documents ?? []).some(isBusy),
  );

  const cvGeneration = documentOf(documents.data, "cv")?.generation ?? null;
  const cvOpen = isOpen(cvGeneration);
  const letterOpen = isOpen(documentOf(documents.data, "letter")?.generation);
  const [cvWasOpen, setCvWasOpen] = useState(cvOpen);
  if (cvOpen !== cvWasOpen) {
    setCvWasOpen(cvOpen);
    if (!cvOpen && cvGeneration?.status !== "failed" && !letterOpen)
      setLetterWindow((count) => count + 1);
  }
  // The letter's own generation is read from here on: the wait for it is over.
  if (letterWindow > 0 && letterOpen) setLetterWindow(0);
  useEffect(() => {
    if (letterWindow === 0) return;
    const timer = setTimeout(() => {
      setLetterWindow(0);
    }, LETTER_FOLLOW_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [letterWindow]);

  const threshold = spend.data
    ? { auto: spend.data.auto, min: spend.data.min_fit }
    : undefined;

  const write = useWriteNow(job.id);
  // Neither document exists: one click writes the CV and the letter that follows it.
  const cvDocument = documentOf(documents.data, "cv");
  const cvReady = (cvDocument?.latest ?? null) !== null;
  const letterWritten =
    (documentOf(documents.data, "letter")?.latest ?? null) !== null;
  const untouched = (["cv", "letter"] as const).every((kind) =>
    ["empty", "under", "manual"].includes(
      cardState(documentOf(documents.data, kind), job.fit, threshold).tag,
    ),
  );

  const offerBoth = documents.data !== undefined && untouched && !noExperience;

  return (
    <Section
      title="Documents"
      actions={
        offerBoth ? (
          <Button
            variant="outline"
            size="sm"
            disabled={write.isPending}
            onClick={() => {
              write.mutate({
                kinds: ["cv", "letter"],
                ...(choice === undefined ? {} : { base_document_id: choice }),
              });
            }}
          >
            Write both now
          </Button>
        ) : null
      }
    >
      {documents.isPending ? (
        <LoadingCard>Loading the documents</LoadingCard>
      ) : documents.isError ? (
        <LoadError query={documents}>
          The documents could not be loaded.
        </LoadError>
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-5">
            {noExperience ? (
              <p
                className="text-sm font-medium"
                data-testid="no-experience-line"
              >
                Add your CV or fill your experience to get documents for each
                posting.{" "}
                <Link
                  to="/profile#your-cv"
                  className="underline underline-offset-4"
                >
                  Open your CVs
                </Link>
              </p>
            ) : null}
            <div className="flex flex-col divide-y">
              {(["cv", "letter"] as const).map((kind) => (
                <PostingDocumentRow
                  key={kind}
                  jobId={job.id}
                  kind={kind}
                  document={documentOf(documents.data, kind)}
                  fit={job.fit}
                  threshold={threshold}
                  cvs={cvs.data?.base_cvs ?? []}
                  choice={choice}
                  onChoose={setChoice}
                  cvReady={cvReady}
                  letterWritten={letterWritten}
                />
              ))}
            </div>
            {offerBoth && write.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  {failureSentence(write.error)}
                </AlertDescription>
              </Alert>
            ) : null}
          </CardContent>
        </Card>
      )}
    </Section>
  );
}
