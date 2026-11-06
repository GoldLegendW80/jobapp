import { ArrowLeft } from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { NotFoundPage } from "@/components/blocks/not-found-page";
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

/** /jobs/:id: everything about one posting; status, notes, fit and details are editable. */
export function JobDetailPage() {
  const { id = "" } = useParams();
  const backTo = useBackTo();
  const job = useJob(id);
  useTitle(job.data ? `${job.data.title} at ${job.data.company}` : "Job");

  if (job.isError && isApiError(job.error) && job.error.status === 404)
    return <NotFoundPage />;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <Button asChild variant="ghost" size="sm" className="w-fit">
        <Link to={backTo}>
          <ArrowLeft aria-hidden="true" />
          Back to jobs
        </Link>
      </Button>
      <JobPosting id={id} />
    </div>
  );
}
