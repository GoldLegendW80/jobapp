import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { JobChat } from "@/components/shared/agents/job-chat";
import {
  WORDING,
  WORK_MODE_WORDS,
  contractWord,
  readableCode,
} from "@/lib/wording";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import type { Save } from "@/features/jobs/api/use-job-editor";
import { payWords } from "@/features/jobs/helpers/pay-words";
import { BadgeHint, FitBadge, PayText, StatusBadge } from "../badges";
import { EditDetailsButton } from "../job-editor/edit-details-button";
import { PostingFacts } from "./posting-facts";
import { PostingTerms } from "./posting-terms";

/** The top of a posting: the company and the buttons, the title, the place, the pay, the
 * badges, the facts, and the competencies and gaps. */
export function PostingHeader({
  job,
  save,
  link,
  title,
  section,
  actions,
}: {
  job: JobDetail;
  save: Save;
  link: string | null;
  /** The level of the posting's title: h1 on its page, h2 in the Jobs page's pane. */
  title: "h1" | "h2";
  /** The level of the headings under the title. */
  section: "h2" | "h3";
  actions?: ReactNode;
}) {
  const pay = payWords(job);
  const workMode = job.work_mode
    ? (WORK_MODE_WORDS[job.work_mode] ?? readableCode(job.work_mode))
    : null;
  const Title = title;
  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm font-medium text-muted-foreground break-words">
            {job.company}
          </p>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {actions}
            <JobChat job={job} />
            <EditDetailsButton job={job} save={save} />
          </div>
        </div>
        <Title
          id="job-title"
          className="text-2xl font-semibold tracking-tight break-words"
        >
          {job.title}
        </Title>
        {job.location ? (
          <p className="text-sm text-muted-foreground break-words">
            {job.location}
          </p>
        ) : null}
      </div>
      <p
        data-testid="posting-pay"
        className={pay ? "text-lg font-semibold tracking-tight" : "text-sm"}
      >
        {pay ? <span className="sr-only">{WORDING.pay}: </span> : null}
        <PayText job={job} />
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={job.status} />
        <FitBadge fit={job.fit} overridden={job.fit_overridden} />
        <Badge variant="outline">{contractWord(job.contract_type)}</Badge>
        {workMode ? (
          <BadgeHint hint={WORDING.workMode}>
            <Badge variant="outline">{workMode}</Badge>
          </BadgeHint>
        ) : null}
      </div>
      <PostingFacts job={job} link={link} />
      <PostingTerms
        label="Competencies"
        terms={job.competencies}
        tone="have"
        heading={section}
      />
      <PostingTerms
        label="Gaps"
        terms={job.gaps}
        tone="gap"
        heading={section}
      />
    </header>
  );
}
