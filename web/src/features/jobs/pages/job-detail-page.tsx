import { useLocation, useParams } from "react-router";

import { NotFoundPage } from "@/components/blocks/not-found-page";
import { Page } from "@/components/blocks/page";
import { isApiError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { JobPosting } from "@/components/shared/jobs/posting/job-posting";
import { useJob } from "../api/use-job";

/** The view to go back to: the query (and /map) the list or map passed along, else the list. */
function useBackTo(): string {
  const location = useLocation();
  const from = (location.state as { from?: unknown } | null)?.from;
  return typeof from === "string" &&
    (from === "" || /^(\/map)?(\?|$)/.test(from))
    ? `/jobs${from}`
    : "/jobs";
}

/**
 * /jobs/:id: everything about one posting, the posting in the main column and what the person
 * acts with (the application, the documents, status and fit) in the side rail.
 */
export function JobDetailPage() {
  const { id = "" } = useParams();
  const backTo = useBackTo();
  const job = useJob(id);
  useTitle(job.data ? `${job.data.title} at ${job.data.company}` : "Job");

  if (job.isError && isApiError(job.error) && job.error.status === 404)
    return <NotFoundPage />;

  return (
    <Page>
      <JobPosting id={id} trail={[{ to: backTo, label: "Jobs" }]} />
    </Page>
  );
}
