import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { viaFeed } from "@/features/opportunities/helpers/sources/source-words";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { formatDate } from "@/features/jobs/helpers/format";
import { sourceLabel } from "@/features/jobs/helpers/labels";
import { hostOf } from "@/features/jobs/helpers/text";

/** The posting's dates, its sources and its link, as a list of facts. */
export function PostingFacts({
  job,
  link,
}: {
  job: JobDetail;
  /** The posting's address when it is a safe http(s) one. */
  link: string | null;
}) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 @lg:grid-cols-4">
      <Fact label="Found">{formatDate(job.found_on)}</Fact>
      {job.published_on ? (
        <Fact label="Published">{formatDate(job.published_on)}</Fact>
      ) : null}
      {job.applied_on ? (
        <Fact label="Applied">{formatDate(job.applied_on)}</Fact>
      ) : null}
      {job.source ? (
        <Fact label="Source">
          <SourceFact source={job.source} link={link} />
        </Fact>
      ) : null}
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
            <span className="break-all text-muted-foreground">{job.url}</span>
          )}
        </Fact>
      ) : null}
    </dl>
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

/**
 * The posting's sources in words. When a freelance feed found it first, "via Remote OK" links
 * back to the posting there, as the feed asks (opportunities O8); another source found too is
 * named after it.
 */
function SourceFact({ source, link }: { source: string; link: string | null }) {
  const via = viaFeed(source);
  if (!via || !link) return <>{sourceLabel(source)}</>;
  const others = source.split(", ").slice(1).join(", ");
  return (
    <>
      <a
        href={link}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 underline underline-offset-4"
      >
        {via}
        <ExternalLink aria-hidden="true" className="size-3" />
        <span className="sr-only">(opens in a new tab)</span>
      </a>
      {others ? `; ${sourceLabel(others)}` : null}
    </>
  );
}
