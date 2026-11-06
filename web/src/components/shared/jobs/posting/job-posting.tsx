import { CircleAlert, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { JobAssessment } from "@/components/shared/agents/job-assessment";
import { useCachedLatestApplication } from "@/features/apply/api/use-cached-latest-application";
import { ApplySection } from "@/components/shared/apply/apply-section";
import { phaseOf } from "@/features/apply/helpers/apply-state";
import { PostingDocumentsSection } from "@/components/shared/documents/posting-documents";
import { describeError, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { useJob } from "@/features/jobs/api/use-job";
import { useJobEditor } from "@/features/jobs/api/use-job-editor";
import { paragraphs, safeHttpUrl } from "@/features/jobs/helpers/text";
import { FilesSection } from "../files-section";
import { FitControl } from "../job-editor/fit-control";
import { NotesEditor } from "../job-editor/notes-editor";
import { StatusControl } from "../job-editor/status-control";
import { ImportedFields } from "./imported-fields";
import { PostingHeader } from "./posting-header";
import { PostingHistory } from "./posting-history";

/**
 * One posting, loaded by id, as both the posting page (/jobs/:id) and the Jobs list's detail pane
 * show it, so the two never drift. `titleLevel` is 2 in the pane, under the Jobs page's h1, and
 * the posting's sections are a level under its title; `actions` go beside the posting's own
 * buttons (the pane's "Open full page").
 */
export function JobPosting({
  id,
  titleLevel = 1,
  actions,
}: {
  id: string;
  titleLevel?: 1 | 2;
  actions?: ReactNode;
}) {
  const job = useJob(id);
  if (job.isPending)
    return (
      <p
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <span className="delayed-appear inline-flex items-center gap-2">
          <Spinner aria-hidden="true" role={undefined} />
          Loading the posting
        </span>
      </p>
    );
  if (job.isError) {
    const gone = isApiError(job.error) && job.error.status === 404;
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertDescription className="flex flex-col gap-3">
          <p>
            {gone
              ? "This posting no longer exists."
              : `This posting could not be loaded. ${describeError(job.error)}`}
          </p>
          {gone ? null : (
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() => void job.refetch()}
              disabled={job.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          )}
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <JobDetailView job={job.data} titleLevel={titleLevel} actions={actions} />
  );
}

/**
 * The open state of "More about this posting" is kept in local storage under this key (the
 * AdvancedSection's), so a person who works in the notes finds them open on the next posting.
 */
const POSTING_MORE_KEY = "posting-more";

/**
 * The posting in the order a person applies (owner, 2026-11-01: "It must be way more easy"): the
 * header with the pay, then "Your application" (Apply and the CV and letter, side by side when
 * the posting is wide enough and nothing is under way), then the posting's text, and the rest
 * folded under "More about this posting". The folded part stays mounted, so unsaved notes or an
 * opened panel survive a fold.
 */
function JobDetailView({
  job,
  titleLevel,
  actions,
}: {
  job: JobDetail;
  titleLevel: 1 | 2;
  actions?: ReactNode;
}) {
  const save = useJobEditor(job.id);
  const link = safeHttpUrl(job.url);
  const legacy = Object.entries(job.legacy);
  // The Apply section reads the application; this only follows its cache. Once an application is
  // under way its live view, form and report need the full width, so the row stacks.
  const latest = useCachedLatestApplication(job.id);
  const sideBySide = phaseOf(latest.data) === "idle";
  const Section = titleLevel === 1 ? "h2" : "h3";
  // Apply and Documents sit inside "Your application", one level below its title.
  const Subsection = titleLevel === 1 ? "h3" : "h4";
  return (
    <article
      aria-labelledby="job-title"
      className="@container flex flex-col gap-8"
    >
      <PostingHeader
        job={job}
        save={save}
        link={link}
        title={titleLevel === 1 ? "h1" : "h2"}
        section={Section}
        actions={actions}
      />

      <section
        aria-labelledby="application-title"
        className="flex flex-col gap-4"
      >
        <Section
          id="application-title"
          className="text-xl font-semibold tracking-tight"
        >
          {WORDING.yourApplication}
        </Section>
        <div
          data-testid="application-row"
          data-layout={sideBySide ? "side" : "stacked"}
          className={cn(
            "grid gap-6",
            sideBySide &&
              "@3xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] @3xl:items-start",
          )}
        >
          <div className="min-w-0 rounded-lg border bg-card p-4 text-card-foreground">
            <ApplySection job={job} heading={Subsection} />
          </div>
          <div className="min-w-0">
            <PostingDocumentsSection job={job} heading={Subsection} />
          </div>
        </div>
      </section>

      <section aria-labelledby="posting-title" className="flex flex-col gap-3">
        <Section
          id="posting-title"
          className="text-lg font-semibold tracking-tight"
        >
          Posting
        </Section>
        {job.posting_text?.trim() ? (
          <div
            data-testid="posting-text"
            className="flex max-w-prose flex-col gap-3 text-sm leading-relaxed"
          >
            {paragraphs(job.posting_text).map((paragraph, index) => (
              <p key={index} className="break-words whitespace-pre-line">
                {paragraph}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No posting text was saved for this job.
            {job.description ? ` Summary: ${job.description}` : ""}
          </p>
        )}
      </section>

      <AdvancedSection
        title={WORDING.moreAboutPosting}
        pageKey={POSTING_MORE_KEY}
        keepMounted
        className="border-t pt-6"
      >
        <div className="flex flex-col gap-8">
          <section
            aria-label="Edit status and fit"
            className="flex flex-col gap-4 rounded-lg border p-4 sm:flex-row sm:items-start sm:gap-8"
          >
            <StatusControl job={job} save={save} />
            <FitControl key={job.id} job={job} save={save} />
          </section>

          <JobAssessment job={job} heading={Section} />

          <NotesEditor key={job.id} job={job} save={save} heading={Section} />

          <FilesSection jobId={job.id} files={job.files} heading={Section} />

          <PostingHistory job={job} heading={Section} />

          {legacy.length > 0 ? <ImportedFields fields={legacy} /> : null}
        </div>
      </AdvancedSection>
    </article>
  );
}
