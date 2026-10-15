import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getProfile, profileKey } from "@/features/profile/api";
import { jobKey, type JobDetail } from "@/features/jobs/api";
import { describeError } from "@/lib/api";

import {
  baseCvsKey,
  getPostingDocuments,
  getSpend,
  listBaseCvs,
  postingDocumentsKey,
  failureSentence,
  spendKey,
  writeNow,
} from "./documents-api";
import { isOpen } from "./labels";
import { PostingDocumentCard } from "./posting-document-card";
import {
  cardState,
  documentOf,
  isBusy,
  LETTER_FOLLOW_MS,
  postingPollInterval,
} from "./posting-documents-state";

/**
 * The "Documents" section of the job page (SPEC-documents.md, "Job detail page"): the posting's
 * CV and letter as two cards. The documents are read again while a generation is open or a page is
 * being drawn, and for a short while after the CV's generation closes, until the letter that follows
 * it has its own. The posting is read again when the work ends: the render that ends it may have
 * moved the posting to `cv_ready`.
 */
export function PostingDocumentsSection({ job }: { job: JobDetail }) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<string>();
  // Counts the CV generations seen closing; 0 when no letter is expected.
  const [letterWindow, setLetterWindow] = useState(0);
  const documents = useQuery({
    queryKey: postingDocumentsKey(job.id),
    queryFn: ({ signal }) => getPostingDocuments(job.id, signal),
    refetchInterval: (current) =>
      postingPollInterval(current.state.data, letterWindow > 0),
  });
  const spend = useQuery({
    queryKey: spendKey(),
    queryFn: ({ signal }) => getSpend(undefined, signal),
  });
  const cvs = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
  });
  const noBase = cvs.data !== undefined && cvs.data.base_cvs.length === 0;
  const nothingWritten =
    documents.data !== undefined && documents.data.documents.length === 0;
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
    enabled: noBase && nothingWritten,
  });
  const noExperience =
    noBase &&
    nothingWritten &&
    profile.data !== undefined &&
    profile.data.sections.experience.employers.length === 0;

  const open = (documents.data?.documents ?? []).some(isBusy);
  const wasOpen = useRef(open);
  useEffect(() => {
    if (wasOpen.current && !open) {
      void queryClient.invalidateQueries({ queryKey: jobKey(job.id) });
      void queryClient.invalidateQueries({ queryKey: spendKey() });
    }
    wasOpen.current = open;
  }, [open, queryClient, job.id]);

  const cvGeneration = documentOf(documents.data, "cv")?.generation ?? null;
  const cvOpen = isOpen(cvGeneration);
  const [cvWasOpen, setCvWasOpen] = useState(cvOpen);
  if (cvOpen !== cvWasOpen) {
    setCvWasOpen(cvOpen);
    if (!cvOpen && cvGeneration?.status !== "failed")
      setLetterWindow((count) => count + 1);
  }
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

  const write = useMutation({
    mutationFn: () =>
      writeNow(job.id, {
        kinds: ["cv", "letter"],
        ...(choice === undefined ? {} : { base_document_id: choice }),
      }),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: postingDocumentsKey(job.id) }),
  });
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

  return (
    <section aria-labelledby="documents-title" className="flex flex-col gap-3">
      <h2 id="documents-title" className="text-lg font-semibold tracking-tight">
        Documents
      </h2>
      {documents.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the documents
        </p>
      ) : documents.isError ? (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-col gap-3">
            <p>
              The documents could not be loaded.{" "}
              {describeError(documents.error)}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              disabled={documents.isFetching}
              onClick={() => void documents.refetch()}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <>
          {noExperience ? (
            <p className="text-sm font-medium" data-testid="no-experience-line">
              Add your CV or fill your experience to get documents for each
              posting.{" "}
              <Link to="/profile/cv" className="underline underline-offset-4">
                Open the CV tab
              </Link>
            </p>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            {(["cv", "letter"] as const).map((kind) => (
              <PostingDocumentCard
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
          {untouched && !noExperience ? (
            <div className="flex flex-col items-start gap-2">
              <Button
                size="sm"
                disabled={write.isPending}
                onClick={() => {
                  write.mutate();
                }}
              >
                Write both now
              </Button>
              {write.isError ? (
                <Alert variant="destructive">
                  <AlertDescription>
                    {failureSentence(write.error)}
                  </AlertDescription>
                </Alert>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
