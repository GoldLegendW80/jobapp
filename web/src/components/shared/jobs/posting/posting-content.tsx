import { ExternalLink } from "lucide-react";

import { Section } from "@/components/blocks/section";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  WORDING,
  WORK_MODE_WORDS,
  contractWord,
  readableCode,
} from "@/lib/wording";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { hostOf, paragraphs } from "@/features/jobs/helpers/text";
import { BadgeHint, FitBadge, StatusBadge } from "../badges";
import { PostingTerms } from "./posting-terms";

/**
 * The posting itself: its status, fit, contract and work mode, its text in short lines, the
 * competencies and gaps behind the fit, and the link to the original.
 */
export function PostingContent({
  job,
  link,
}: {
  job: JobDetail;
  /** The posting's address when it is a safe http(s) one. */
  link: string | null;
}) {
  const workMode = job.work_mode
    ? (WORK_MODE_WORDS[job.work_mode] ?? readableCode(job.work_mode))
    : null;
  return (
    <Section title="Posting">
      <Card>
        <CardContent className="flex flex-col gap-6">
          <div
            data-testid="posting-badges"
            className="flex flex-wrap items-center gap-2"
          >
            <StatusBadge status={job.status} />
            <FitBadge fit={job.fit} overridden={job.fit_overridden} />
            <Badge variant="outline">{contractWord(job.contract_type)}</Badge>
            {workMode ? (
              <BadgeHint hint={WORDING.workMode}>
                <Badge variant="outline">{workMode}</Badge>
              </BadgeHint>
            ) : null}
          </div>
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
            <p className="max-w-prose text-sm text-muted-foreground">
              No posting text was saved for this job.
              {job.description ? ` Summary: ${job.description}` : ""}
            </p>
          )}
          <PostingTerms
            label="Competencies"
            terms={job.competencies}
            tone="have"
          />
          <PostingTerms label="Gaps" terms={job.gaps} tone="gap" />
          {job.url ? (
            <p data-testid="posting-link" className="text-sm">
              <span className="text-muted-foreground">Original posting: </span>
              {link ? (
                <a
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-medium underline underline-offset-4"
                >
                  {hostOf(link)}
                  <ExternalLink aria-hidden="true" className="size-3.5" />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              ) : (
                <span className="break-all">{job.url}</span>
              )}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
