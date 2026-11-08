import { CircleAlert, Info } from "lucide-react";
import { Link } from "react-router";

import type { Notice } from "@/features/jobs/api/notices-api";
import { cn } from "@/lib/utils";

/**
 * What to know about a posting before the click (SPEC-jobs, "Before the click"): what its text
 * says against the person's profile, its closing date, a posting of another company with the
 * same text, and the person's applications to the company. Facts, never a block: nothing here
 * disables or hides an action. In full (the posting page, under its header) each notice keeps
 * the posting's own words and a link to the other posting it names; `compact` (the Discover
 * card, the Apply confirmation) shows the first `max` sentences alone, the warnings first.
 */
export function PostingNotices({
  notices,
  compact = false,
  max,
  className,
}: {
  notices: readonly Notice[] | undefined;
  compact?: boolean;
  /** How many sentences a compact list shows; the rest are counted. */
  max?: number;
  className?: string;
}) {
  if (!notices || notices.length === 0) return null;
  const shown = max === undefined ? notices : notices.slice(0, max);
  const hidden = notices.length - shown.length;
  return (
    <ul
      aria-label="Before you apply"
      data-testid="posting-notices"
      className={cn(
        "flex min-w-0 flex-col",
        compact ? "gap-1 text-sm" : "max-w-prose gap-3 text-sm",
        className,
      )}
    >
      {shown.map((notice) => (
        <li
          key={`${notice.kind}:${notice.code}:${notice.text}`}
          data-level={notice.level}
          className="flex min-w-0 items-start gap-2"
        >
          <NoticeIcon warning={notice.level === "warning"} />
          <div className="flex min-w-0 flex-col gap-1">
            <p className={cn(compact && "line-clamp-2")}>{notice.text}</p>
            {!compact && notice.quote ? (
              <blockquote className="border-l-2 pl-3 text-muted-foreground italic [overflow-wrap:anywhere]">
                “{notice.quote}”
              </blockquote>
            ) : null}
            {!compact && notice.job_id ? (
              <Link
                to={`/jobs/${notice.job_id}`}
                className="w-fit text-muted-foreground underline underline-offset-4 hover:text-foreground"
              >
                {notice.kind === "history"
                  ? "Open that application"
                  : "Open the other posting"}
              </Link>
            ) : null}
          </div>
        </li>
      ))}
      {hidden > 0 ? (
        <li className="pl-6 text-muted-foreground">
          {hidden === 1 ? "1 more to know" : `${String(hidden)} more to know`}
        </li>
      ) : null}
    </ul>
  );
}

function NoticeIcon({ warning }: { warning: boolean }) {
  const Icon = warning ? CircleAlert : Info;
  return (
    <Icon
      aria-hidden="true"
      className={cn(
        "mt-0.5 size-4 shrink-0",
        warning ? "text-warning" : "text-muted-foreground",
      )}
    />
  );
}
