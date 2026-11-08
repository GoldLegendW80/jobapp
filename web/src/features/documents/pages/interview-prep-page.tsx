import { MessageSquareText } from "lucide-react";
import { useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { NotFoundPage } from "@/components/blocks/not-found-page";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PrepareButton } from "@/components/shared/documents/interview-prep/prepare-button";
import { useJob } from "@/features/jobs/api/use-job";
import { useTitle } from "@/hooks/use-title";
import { isApiError } from "@/lib/api";

import { useInterviewPrep } from "../api/use-interview-prep";
import { PrepDebrief } from "../components/interview-prep/prep-debrief";
import { PrepPractice } from "../components/interview-prep/prep-practice";
import {
  PrepAsk,
  PrepGaps,
  PrepQuestions,
} from "../components/interview-prep/prep-questions";
import { PrepStories } from "../components/interview-prep/prep-stories";

/**
 * /jobs/:id/interview: the interview preparation of a posting. The questions to expect, the gaps,
 * the stories (kept for the profile on accept), the questions to ask and the Debrief in the main
 * column; Practice in the side rail. Prepare again rewrites it and keeps the Debrief.
 */
export function InterviewPrepPage() {
  const { id = "" } = useParams();
  const job = useJob(id);
  const prep = useInterviewPrep(id);
  useTitle(
    job.data
      ? `Interview preparation for ${job.data.title} at ${job.data.company}`
      : "Interview preparation",
  );
  const missing = (error: unknown) => isApiError(error) && error.status === 404;
  if (missing(job.error) || missing(prep.error)) return <NotFoundPage />;

  const trail = [
    { to: "/jobs", label: "Jobs" },
    { to: `/jobs/${id}`, label: job.data?.title ?? "Posting" },
  ];
  const document = prep.data?.document ?? null;
  const writing = prep.data?.writing?.status;
  const written = document !== null && document.versions > 0;
  return (
    <Page>
      <PageHeader
        trail={trail}
        title="Interview preparation"
        description={
          job.data ? `${job.data.title} at ${job.data.company}` : undefined
        }
        actions={
          written && writing !== "running" ? (
            <PrepareButton jobId={id} label="Prepare again" variant="outline" />
          ) : null
        }
      />
      {prep.isPending ? (
        <LoadingCard>Loading the interview preparation</LoadingCard>
      ) : prep.isError ? (
        <LoadError query={prep}>
          The interview preparation could not be loaded.
        </LoadError>
      ) : !written && writing === "running" ? (
        <LoadingCard>
          Preparing your interview. This takes about a minute.
        </LoadingCard>
      ) : !written ? (
        <EmptyState
          icon={MessageSquareText}
          title={
            writing === "failed"
              ? "The preparation could not be written"
              : "Not prepared yet"
          }
          description="Get the questions to expect, your gaps, stories with your own figures and questions to ask. Nothing is searched on the web."
          actions={
            <PrepareButton jobId={id} label="Prepare for the interview" />
          }
        />
      ) : (
        <PageBody
          aside={
            <PrepPractice
              jobId={id}
              documentId={document.id}
              messages={prep.data.practice}
              answering={prep.data.practice_open}
            />
          }
        >
          {writing === "running" ? (
            <LoadingCard>
              Preparing again. This takes about a minute.
            </LoadingCard>
          ) : null}
          <PrepQuestions content={document.draft} />
          <PrepGaps content={document.draft} />
          <PrepStories
            jobId={id}
            prep={{ ...document, proposal: prep.data.stories_proposal }}
          />
          <PrepAsk content={document.draft} />
          <PrepDebrief jobId={id} entries={document.draft.debrief} />
        </PageBody>
      )}
    </Page>
  );
}
