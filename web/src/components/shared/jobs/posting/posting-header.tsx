import type { ReactNode } from "react";

import { PageHeader, type Crumb } from "@/components/blocks/page-header";
import { JobChat } from "@/components/shared/agents/job-chat";
import { WORDING } from "@/lib/wording";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import type { Save } from "@/features/jobs/api/use-job-editor";
import { payWords } from "@/features/jobs/helpers/pay-words";
import { PayText } from "../badges";
import { LivenessBadge } from "../liveness-badge";
import { EditDetailsButton } from "../job-editor/edit-details-button";

/**
 * The top of a posting: its title, then the company, the place and the pay on one line, and Chat
 * and Edit details. On its page the title is the page's h1 under the breadcrumb; in the Jobs list's
 * pane, under the Jobs page's h1, it is an h2 and the pane's `actions` come first.
 */
export function PostingHeader({
  job,
  save,
  titleId,
  level,
  trail,
  actions,
}: {
  job: JobDetail;
  save: Save;
  /** The id of the title, which names the posting's article. */
  titleId: string;
  level: 1 | 2;
  trail?: readonly Crumb[];
  actions?: ReactNode;
}) {
  const description = <PostingSummary job={job} />;
  const buttons = (
    <>
      {actions}
      <JobChat job={job} />
      <EditDetailsButton job={job} save={save} />
    </>
  );
  if (level === 1)
    return (
      <PageHeader
        trail={trail}
        title={job.title}
        titleId={titleId}
        description={description}
        actions={buttons}
      />
    );
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <h2
          id={titleId}
          className="text-2xl font-medium tracking-tight [overflow-wrap:anywhere]"
        >
          {job.title}
        </h2>
        <p className="max-w-prose text-sm text-muted-foreground">
          {description}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">{buttons}</div>
    </header>
  );
}

/** "Lumen Freight, Lyon, France, 55,000 to 65,000 EUR a year": the pay stands out, or says it is not given. */
function PostingSummary({ job }: { job: JobDetail }) {
  const where = [job.company, job.location].filter(Boolean).join(", ");
  return (
    <>
      <span className="[overflow-wrap:anywhere]">{where}</span>,{" "}
      <span data-testid="posting-pay">
        {payWords(job) ? (
          <span className="sr-only">{WORDING.pay}: </span>
        ) : null}
        <PayText job={job} className="font-medium text-foreground" />
      </span>
      <LivenessBadge
        liveness={job.liveness}
        status={job.status}
        className="ml-2"
      />
    </>
  );
}
