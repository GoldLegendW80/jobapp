import { SearchX } from "lucide-react";
import { useId, type ReactNode } from "react";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { PageBody } from "@/components/blocks/page";
import type { Crumb } from "@/components/blocks/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { JobAssessment } from "@/components/shared/agents/job-assessment";
import { ApplySection } from "@/components/shared/apply/apply-section";
import { FollowUpLine } from "@/components/shared/apply/follow-up-line";
import { PostingDocumentsSection } from "@/components/shared/documents/posting-documents";
import { InterviewPrepSection } from "@/components/shared/documents/interview-prep/interview-prep-section";
import { OfferNotesSection } from "@/components/shared/documents/offer-notes/offer-notes-section";
import { isApiError } from "@/lib/api";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useJob } from "@/features/jobs/api/use-job";
import { useJobEditor } from "@/features/jobs/api/use-job-editor";
import { safeHttpUrl } from "@/features/jobs/helpers/text";
import { AssessSection } from "../assess/assess-section";
import { AssessmentDetails } from "../assess/assessment-details";
import { FilesSection } from "../files-section";
import { NotesEditor } from "../job-editor/notes-editor";
import { StatusAndFit } from "../job-editor/status-and-fit";
import { ImportedFields } from "./imported-fields";
import { PostingContent } from "./posting-content";
import { PostingFacts } from "./posting-facts";
import { PostingHeader } from "./posting-header";
import { PostingHistory } from "./posting-history";
import { PostingLiveness } from "./posting-liveness";
import { PostingNotices } from "./posting-notices";

/** The rhythm of Page between the header and the body, for the pane that has no Page. */
const RHYTHM = "flex flex-col gap-6 sm:gap-8";

/**
 * One posting, loaded by id, as both the posting page (/jobs/:id) and the Jobs list's detail pane
 * show it, so the two never drift. `titleLevel` is 2 in the pane, under the Jobs page's h1;
 * `trail` is the page's breadcrumb, the list above it; `actions` go first among the posting's own
 * buttons (the pane's "Open full page").
 */
export function JobPosting({
  id,
  titleLevel = 1,
  trail,
  actions,
}: {
  id: string;
  titleLevel?: 1 | 2;
  trail?: readonly Crumb[];
  actions?: ReactNode;
}) {
  const job = useJob(id);
  if (job.isPending) return <PostingSkeleton trail={trail !== undefined} />;
  if (job.isError)
    return isApiError(job.error) && job.error.status === 404 ? (
      <EmptyState
        icon={SearchX}
        title="This posting no longer exists"
        description="It was deleted. Choose another posting in the list."
      />
    ) : (
      <LoadError query={job}>This posting could not be loaded.</LoadError>
    );
  return (
    <JobDetailView
      job={job.data}
      titleLevel={titleLevel}
      trail={trail}
      actions={actions}
    />
  );
}

/**
 * The posting in the main column: its text, competencies and gaps, the offer from the interview
 * on, then the person's notes, its files and its history. The side rail holds what the person acts with: the application first,
 * the documents, the status and fit, the assessment, then the dates and sources.
 */
function JobDetailView({
  job,
  titleLevel,
  trail,
  actions,
}: {
  job: JobDetail;
  titleLevel: 1 | 2;
  trail?: readonly Crumb[];
  actions?: ReactNode;
}) {
  const titleId = useId();
  const save = useJobEditor(job.id);
  const link = safeHttpUrl(job.url);
  const legacy = Object.entries(job.legacy);
  return (
    <article aria-labelledby={titleId} className={RHYTHM}>
      <PostingHeader
        job={job}
        save={save}
        titleId={titleId}
        level={titleLevel}
        trail={trail}
        actions={actions}
      />
      <PostingNotices notices={job.notices} />
      <PageBody
        lead={<ApplySection job={job} />}
        aside={
          <>
            <FollowUpLine jobId={job.id} />
            <PostingDocumentsSection job={job} />
            <AssessSection job={job} />
            <InterviewPrepSection job={job} />
            <StatusAndFit job={job} save={save} />
            <JobAssessment job={job} />
            <PostingFacts job={job} link={link} />
            <PostingLiveness job={job} />
          </>
        }
      >
        <PostingContent job={job} link={link} />
        <AssessmentDetails job={job} />
        <OfferNotesSection job={job} />
        <NotesEditor key={job.id} job={job} save={save} />
        <FilesSection jobId={job.id} files={job.files} />
        <PostingHistory job={job} />
        {legacy.length > 0 ? <ImportedFields fields={legacy} /> : null}
      </PageBody>
    </article>
  );
}

/** The posting's place while it loads: its header, then its sections as cards of grey lines. */
function PostingSkeleton({ trail }: { trail: boolean }) {
  return (
    <div className={RHYTHM}>
      <p role="status" aria-live="polite" className="sr-only">
        Loading the posting
      </p>
      <div className="flex flex-col gap-3">
        {trail ? <Skeleton className="h-5 w-28" /> : null}
        <Skeleton className="h-9 w-full max-w-lg" />
        <Skeleton className="h-4 w-full max-w-sm" />
      </div>
      <PageBody
        aside={
          <>
            <SectionSkeleton lines={3} />
            <SectionSkeleton lines={4} />
          </>
        }
      >
        <SectionSkeleton lines={9} />
        <SectionSkeleton lines={3} />
      </PageBody>
    </div>
  );
}

function SectionSkeleton({ lines }: { lines: number }) {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-6 w-32" />
      <Card>
        <CardContent className="flex flex-col gap-3">
          {Array.from({ length: lines }, (_, index) => (
            <Skeleton key={index} className="h-4 max-w-prose last:w-2/3" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
