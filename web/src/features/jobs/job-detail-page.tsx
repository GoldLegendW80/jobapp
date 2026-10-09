import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  LoaderCircle,
  RotateCw,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NotFoundPage } from "@/features/shell/not-found-page";
import { describeError, isApiError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { getJob, jobKey, type JobDetail } from "./api";
import { FitBadge, StatusBadge } from "./badges";
import { FilesSection } from "./files-section";
import {
  EditDetailsButton,
  FitControl,
  NotesEditor,
  StatusControl,
} from "./job-editor";
import { useJobEditor } from "./use-job-editor";
import { formatDate, formatDateTime } from "./format";
import {
  describeEvent,
  hostOf,
  paragraphs,
  prettyJson,
  safeHttpUrl,
} from "./text";

/** The list view to go back to: the query the list passed along, else the plain list. */
function useBackTo(): string {
  const location = useLocation();
  const from = (location.state as { from?: unknown } | null)?.from;
  return typeof from === "string" && (from === "" || from.startsWith("?"))
    ? `/jobs${from}`
    : "/jobs";
}

/** /jobs/:id: everything about one posting; status, notes, fit and details are editable. */
export function JobDetailPage() {
  const { id = "" } = useParams();
  const backTo = useBackTo();
  const job = useQuery({
    queryKey: jobKey(id),
    queryFn: ({ signal }) => getJob(id, signal),
  });
  useTitle(job.data ? `${job.data.title} at ${job.data.company}` : "Job");

  const back = (
    <Button asChild variant="ghost" size="sm" className="w-fit">
      <Link to={backTo}>
        <ArrowLeft aria-hidden="true" />
        Back to jobs
      </Link>
    </Button>
  );

  if (job.isError && isApiError(job.error) && job.error.status === 404)
    return <NotFoundPage />;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      {back}
      {job.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <span className="delayed-appear inline-flex items-center gap-2">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Loading the posting
          </span>
        </p>
      ) : job.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>This posting could not be loaded. {describeError(job.error)}</p>
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
          </AlertDescription>
        </Alert>
      ) : (
        <JobDetailView job={job.data} />
      )}
    </div>
  );
}

function Terms({
  label,
  terms,
  tone,
}: {
  label: string;
  terms: string[];
  tone: "have" | "gap";
}) {
  if (terms.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{label}</h3>
      <ul aria-label={label} className="flex flex-wrap gap-1.5">
        {terms.map((term) => (
          <li key={term}>
            <Badge
              variant="outline"
              className={
                tone === "have"
                  ? "border-success/30 bg-success/10 text-foreground"
                  : "border-warning/40 bg-warning-muted text-foreground"
              }
            >
              {term}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

function JobDetailView({ job }: { job: JobDetail }) {
  const save = useJobEditor(job.id);
  const link = safeHttpUrl(job.url);
  const legacy = Object.entries(job.legacy);
  return (
    <article aria-labelledby="job-title" className="flex flex-col gap-8">
      <header className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-medium text-muted-foreground break-words">
              {job.company}
            </p>
            <EditDetailsButton job={job} save={save} />
          </div>
          <h1
            id="job-title"
            className="text-2xl font-semibold tracking-tight break-words"
          >
            {job.title}
          </h1>
          {job.location ? (
            <p className="text-sm text-muted-foreground break-words">
              {job.location}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={job.status} />
          <FitBadge fit={job.fit} overridden={job.fit_overridden} />
          <Badge variant="outline">{job.contract_type}</Badge>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <Fact label="Found">{formatDate(job.found_on)}</Fact>
          {job.published_on ? (
            <Fact label="Published">{formatDate(job.published_on)}</Fact>
          ) : null}
          {job.applied_on ? (
            <Fact label="Applied">{formatDate(job.applied_on)}</Fact>
          ) : null}
          {job.source ? <Fact label="Source">{job.source}</Fact> : null}
          {job.url ? (
            <Fact label="Posting">
              {link ? (
                <a
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 underline underline-offset-4"
                >
                  {hostOf(link)}
                  <ExternalLink aria-hidden="true" className="size-3" />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              ) : (
                <span className="break-all text-muted-foreground">
                  {job.url}
                </span>
              )}
            </Fact>
          ) : null}
        </dl>
        <Terms label="Competencies" terms={job.competencies} tone="have" />
        <Terms label="Gaps" terms={job.gaps} tone="gap" />
      </header>

      <section
        aria-label="Edit status and fit"
        className="flex flex-col gap-4 rounded-lg border p-4 sm:flex-row sm:items-start sm:gap-8"
      >
        <StatusControl job={job} save={save} />
        <FitControl key={job.id} job={job} save={save} />
      </section>

      <NotesEditor key={job.id} job={job} save={save} />

      <section aria-labelledby="posting-title" className="flex flex-col gap-3">
        <h2 id="posting-title" className="text-lg font-semibold tracking-tight">
          Posting
        </h2>
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

      <FilesSection jobId={job.id} files={job.files} />

      <section aria-labelledby="history-title" className="flex flex-col gap-3">
        <h2 id="history-title" className="text-lg font-semibold tracking-tight">
          History
        </h2>
        {job.events.length === 0 ? (
          <p className="text-sm text-muted-foreground">No events yet.</p>
        ) : (
          <ol
            aria-label="History, newest first"
            className="flex flex-col gap-3 border-l pl-4"
          >
            {job.events.map((event) => (
              <li key={event.id} className="relative flex flex-col gap-0.5">
                <span
                  aria-hidden="true"
                  className="absolute top-1.5 -left-[1.3rem] size-2 rounded-full bg-muted-foreground"
                />
                <span className="text-sm">
                  {describeEvent(event.kind, event.data)}
                </span>
                <time
                  dateTime={event.at}
                  className="text-xs text-muted-foreground"
                >
                  {formatDateTime(event.at)}
                </time>
              </li>
            ))}
          </ol>
        )}
        {job.events_total > job.events.length ? (
          <p className="text-xs text-muted-foreground">
            Showing the newest {job.events.length} of {job.events_total} events.
          </p>
        ) : null}
      </section>

      {legacy.length > 0 ? (
        <details className="group rounded-lg border">
          <summary className="flex cursor-pointer items-center gap-1 px-3 py-2 text-sm font-medium select-none">
            <ChevronRight
              aria-hidden="true"
              className="size-4 transition-transform group-open:rotate-90"
            />
            Imported fields
          </summary>
          <dl className="flex flex-col gap-3 border-t px-3 py-3">
            {legacy.map(([key, value]) => (
              <div key={key} className="flex flex-col gap-0.5">
                <dt className="text-xs font-medium text-muted-foreground">
                  {key}
                </dt>
                <dd className="text-sm break-words whitespace-pre-wrap">
                  {typeof value === "string" ? value : prettyJson(value)}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
    </article>
  );
}
